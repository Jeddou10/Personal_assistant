"""Local-day boundaries and immutable read-only calendar presentation data."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from assistant_app.domain.errors import AppError


@dataclass(frozen=True)
class CalendarEntry:
    title: str
    start: datetime | None
    end: datetime | None
    all_day: bool
    location: str
    continues_before: bool = False
    continues_after: bool = False


@dataclass(frozen=True)
class CalendarDay:
    date: date
    entries: tuple[CalendarEntry, ...]


@dataclass(frozen=True)
class CalendarSnapshot:
    days: tuple[CalendarDay, ...]
    timezone_label: str
    truncated: bool
    skipped: int


def local_midnight(day: date, zone: tzinfo | None = None) -> datetime:
    # Convert each boundary separately: DST can change between two midnights.
    naive = datetime.combine(day, time.min)
    return naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()


def day_window(today: date | None = None, *, zone: tzinfo | None = None):
    today = today or datetime.now(zone).date()
    return local_midnight(today, zone), local_midnight(today + timedelta(days=2), zone)


def important_mail_prompt(today: date | None = None) -> str:
    start, _ = day_window(today)
    end = local_midnight(start.date() + timedelta(days=1))
    query = f"is:important after:{int(start.timestamp())} before:{int(end.timestamp())}"
    return (
        f"Summarize my important emails for today, {start.date().isoformat()}, using my computer's local day. "
        f"Search Gmail with exactly this query: {query}. Importance means Gmail's Important label, "
        "not only unread messages. Read matching messages before summarizing; batch independent read_mail calls "
        "in one round when possible. Give senders, subjects, "
        "key points, deadlines and actions needed. Say clearly if none match or results are limited. "
        "Do not send messages or propose any writes."
    )


def calendar_snapshot(result: dict, today: date, *, zone: tzinfo | None = None) -> CalendarSnapshot:
    if not isinstance(result, dict) or not isinstance(result.get("events"), list):
        raise AppError("Calendar returned an invalid event list.")
    dates = (today, today + timedelta(days=1))
    buckets = [[], []]
    skipped = 0
    for event in result["events"][:50]:
        try:
            if not isinstance(event, dict):
                raise ValueError()
            if event.get("status") == "cancelled":
                continue
            title = str(event.get("summary") or "(Untitled event)")[:240]
            location = str(event.get("location") or "")[:500]
            start_data, end_data = event["start"], event["end"]
            all_day = "date" in start_data
            if all_day:
                first, last = date.fromisoformat(start_data["date"]), date.fromisoformat(end_data["date"])
                if last <= first:
                    raise ValueError()
            else:
                start = datetime.fromisoformat(start_data["dateTime"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(end_data["dateTime"].replace("Z", "+00:00"))
                if start.utcoffset() is None or end.utcoffset() is None or end.timestamp() <= start.timestamp():
                    raise ValueError()
                start, end = start.astimezone(zone), end.astimezone(zone)
            for index, day in enumerate(dates):
                lower, upper = local_midnight(day, zone), local_midnight(day + timedelta(days=1), zone)
                if all_day and first <= day < last:
                    buckets[index].append(
                        CalendarEntry(title, None, None, True, location, first < day, last > day + timedelta(days=1))
                    )
                elif not all_day and start.timestamp() < upper.timestamp() and end.timestamp() > lower.timestamp():
                    buckets[index].append(
                        CalendarEntry(
                            title,
                            max(start, lower, key=lambda value: value.timestamp()),
                            min(end, upper, key=lambda value: value.timestamp()),
                            False,
                            location,
                            start.timestamp() < lower.timestamp(),
                            end.timestamp() > upper.timestamp(),
                        )
                    )
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            skipped += 1
    days = tuple(
        CalendarDay(
            day,
            tuple(
                sorted(
                    entries,
                    key=lambda entry: (
                        not entry.all_day,
                        entry.start.timestamp() if entry.start else 0,
                        entry.title.casefold(),
                    ),
                )
            ),
        )
        for day, entries in zip(dates, buckets)
    )
    return CalendarSnapshot(
        days,
        "Computer local time" if zone is None else str(zone),
        bool(result.get("truncated")) or len(result["events"]) > 50,
        skipped,
    )
