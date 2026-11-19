"""Provider contract tests: all transport and secret access are fake."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assistant_app.domain.errors import AppError
from assistant_app.infrastructure import net, providers

TOOLS = [
    {
        "name": "read_mail",
        "description": "Read a message",
        "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    }
]
SHOW = {
    "capabilities": ["completion", "tools"],
    "details": {"format": "gguf"},
    "model_info": {"general.architecture": "qwen2"},
}
TEXT = {"content": [{"type": "text", "text": "Hello"}]}
CHAT = {"done": True, "message": {"role": "assistant", "content": "Hello"}}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.transport_patch = patch.object(net, "request_json")
        self.transport = self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)
        self.store = Mock()
        self.store.get_secret.return_value = "fake-key"
        self.settings = SimpleNamespace(
            provider="anthropic", model="claude-sonnet-5-5", local_url="http://127.0.0.1:11434"
        )

    def complete(self, messages=None, tools=None):
        return providers.complete(
            self.settings,
            self.store,
            "Be helpful",
            messages if messages is not None else [{"role": "user", "content": "Hi"}],
            TOOLS if tools is None else tools,
        )

    def local(self):
        self.settings.provider = "ollama"
        self.settings.model = "qwen2.5:7b"
        self.transport.side_effect = [copy.deepcopy(SHOW), copy.deepcopy(CHAT)]

    def test_anthropic_request_and_default(self):
        del self.settings.model
        self.transport.return_value = TEXT
        self.assertEqual(self.complete(), TEXT["content"])
        args, kwargs = self.transport.call_args
        self.assertEqual(args, ("POST", "https://api.anthropic.com/v1/messages"))
        self.assertEqual(
            kwargs["headers"],
            {"x-api-key": "fake-key", "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        self.assertEqual(kwargs["data"]["model"], "claude-sonnet-5-5")
        self.assertEqual(kwargs["data"]["max_tokens"], 2048)
        self.assertEqual(kwargs["data"]["system"], "Be helpful")
        self.assertEqual(kwargs["data"]["tools"], TOOLS)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertFalse(kwargs["local"])
        self.store.get_secret.assert_called_once_with("anthropic_key")

    def test_configurable_cloud_model_and_no_tools(self):
        self.settings.model = "another-model"
        self.transport.return_value = TEXT
        self.complete(tools=[])
        self.assertEqual(self.transport.call_args.kwargs["data"]["model"], "another-model")
        self.assertNotIn("tools", self.transport.call_args.kwargs["data"])

    def test_absent_or_unsafe_key_never_sends(self):
        for key in (None, "", "   ", "key\r\ninjected: value", 12):
            with self.subTest(key=key):
                self.store.get_secret.return_value = key
                with self.assertRaises(AppError):
                    self.complete()
        self.transport.assert_not_called()

    def test_anthropic_tools_are_normalized(self):
        block = {"type": "tool_use", "id": "tool_1", "name": "read_mail", "input": {"id": "abc"}}
        self.transport.return_value = {"content": [TEXT["content"][0], block]}
        self.assertEqual(self.complete(), [TEXT["content"][0], block])

    def test_thinking_tool_result_roundtrip_and_local_privacy(self):
        for thinking_text in ("", "private reasoning"):
            blocks = [
                {"type": "thinking", "thinking": thinking_text, "signature": "opaque+/signature=="},
                {"type": "redacted_thinking", "data": "opaque+/redacted=="},
                {"type": "text", "text": "Checking"},
                {"type": "tool_use", "id": "one", "name": "read_mail", "input": {"id": "1"}},
            ]
            self.settings.provider = "anthropic"
            self.transport.side_effect = [{"content": copy.deepcopy(blocks)}, TEXT]
            generated = self.complete()
            self.assertEqual(generated, blocks)
            history = [
                {"role": "user", "content": "Read"},
                {"role": "assistant", "content": generated},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "one", "content": "Result"}]},
            ]
            original = copy.deepcopy(history)
            self.assertEqual(self.complete(history), TEXT["content"])
            self.assertEqual(self.transport.call_args.kwargs["data"]["messages"], original)
            self.local()
            self.complete(history)
            local = self.transport.call_args.kwargs["data"]["messages"]
            self.assertEqual(
                local[2],
                {
                    "role": "assistant",
                    "content": "Checking",
                    "tool_calls": [{"function": {"name": "read_mail", "arguments": {"id": "1"}}}],
                },
            )
            self.assertEqual(local[3], {"role": "tool", "tool_name": "read_mail", "content": "Result"})
            self.assertEqual(history, original)

    def test_invalid_thinking_rejected_in_generated_and_history(self):
        for block in (
            {"type": "thinking", "thinking": ""},
            {"type": "thinking", "thinking": 1, "signature": "sig"},
            {"type": "thinking", "thinking": "", "signature": ""},
            {"type": "thinking", "thinking": "", "signature": {}},
            {"type": "redacted_thinking", "data": None},
            {"type": "redacted_thinking", "data": ""},
            {"type": "redacted_thinking", "data": "x" * 262144},
        ):
            with self.subTest(block_type=block["type"]):
                self.transport.return_value = {"content": [block]}
                with self.assertRaises(AppError):
                    self.complete()
                self.transport.reset_mock()
                with self.assertRaises(AppError):
                    self.complete([{"role": "assistant", "content": [block]}])
                self.transport.assert_not_called()

    def history(self):
        return [
            {"role": "user", "content": "Read these"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking"},
                    {"type": "tool_use", "id": "one", "name": "read_mail", "input": {"id": "1"}},
                    {"type": "tool_use", "id": "two", "name": "read_mail", "input": {"id": "2"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "one", "content": [{"type": "text", "text": "First"}]},
                    {"type": "tool_result", "tool_use_id": "two", "content": "Unavailable", "is_error": True},
                    {"type": "text", "text": "Summarize"},
                ],
            },
        ]

    def test_anthropic_history_preserved_without_mutation(self):
        messages = self.history()
        original = copy.deepcopy(messages)
        self.transport.return_value = TEXT
        self.complete(messages)
        self.assertEqual(self.transport.call_args.kwargs["data"]["messages"], original)
        self.assertEqual(messages, original)

    def test_local_conversion_and_isolation(self):
        self.local()
        self.assertEqual(self.complete(self.history()), TEXT["content"])
        self.store.get_secret.assert_not_called()
        show, chat = self.transport.call_args_list
        self.assertEqual(show.args, ("POST", "http://127.0.0.1:11434/api/show"))
        self.assertEqual(show.kwargs["data"], {"model": "qwen2.5:7b"})
        self.assertEqual(chat.args[1], "http://127.0.0.1:11434/api/chat")
        for call in (show, chat):
            self.assertTrue(call.kwargs["local"])
            self.assertIsNone(call.kwargs["headers"])
            self.assertEqual(call.kwargs["timeout"], 60)
        data = chat.kwargs["data"]
        self.assertFalse(data["stream"])
        self.assertEqual(
            data["tools"][0],
            {
                "type": "function",
                "function": {
                    "name": "read_mail",
                    "description": "Read a message",
                    "parameters": TOOLS[0]["input_schema"],
                },
            },
        )
        self.assertEqual(
            data["messages"],
            [
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Read these"},
                {
                    "role": "assistant",
                    "content": "Checking",
                    "tool_calls": [
                        {"function": {"name": "read_mail", "arguments": {"id": "1"}}},
                        {"function": {"name": "read_mail", "arguments": {"id": "2"}}},
                    ],
                },
                {"role": "tool", "tool_name": "read_mail", "content": "First"},
                {"role": "tool", "tool_name": "read_mail", "content": "Tool error: Unavailable"},
                {"role": "user", "content": "Summarize"},
            ],
        )

    def test_local_generated_calls_have_unique_ids_and_roundtrip(self):
        self.local()
        reply = {
            "done": True,
            "message": {
                "role": "assistant",
                "content": "Reading",
                "tool_calls": [
                    {"function": {"name": "read_mail", "arguments": {"id": "a"}}},
                    {"function": {"name": "read_mail", "arguments": {"id": "b"}}},
                ],
            },
        }
        self.transport.side_effect = [SHOW, reply, SHOW, CHAT]
        blocks = self.complete()
        self.assertEqual(blocks[0], {"type": "text", "text": "Reading"})
        self.assertNotEqual(blocks[1]["id"], blocks[2]["id"])
        self.assertEqual(blocks[1]["input"], {"id": "a"})
        history = [
            {"role": "user", "content": "Read"},
            {"role": "assistant", "content": blocks},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": "result"} for b in blocks[1:]],
            },
        ]
        self.assertEqual(self.complete(history), TEXT["content"])

    def test_ipv6_loopback(self):
        self.local()
        self.settings.local_url = "http://[::1]:11434"
        self.complete()
        self.assertEqual(self.transport.call_args.args[1], "http://[::1]:11434/api/chat")

    def test_malicious_endpoints_rejected_before_transport(self):
        self.local()
        for endpoint in (
            "http://localhost:11434",
            "http://127.1",
            "http://2130706433",
            "http://0.0.0.0",
            "http://127.0.0.1.evil",
            "https://127.0.0.1",
            "http://[::ffff:127.0.0.1]",
            "http://user@127.0.0.1",
            "http://127.0.0.1/",
            "http://127.0.0.1/api",
            "http://127.0.0.1?",
            "http://127.0.0.1#",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://127.0.0.1\n",
            " http://127.0.0.1",
            "http://127.0.0.1\\@evil",
            None,
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(AppError):
                self.settings.local_url = endpoint
                self.complete()
        self.transport.assert_not_called()
        self.store.get_secret.assert_not_called()

    def test_cloud_names_and_remote_identifiers_rejected(self):
        self.local()
        for model in (
            "gpt-oss:120b-cloud",
            "model:cloud",
            "model-CLOUD",
            "https://ollama.com/model",
            "ollama.com/library/model",
            "../model",
            "model@remote",
            "model\n",
        ):
            with self.subTest(model=model), self.assertRaises(AppError):
                self.settings.model = model
                self.complete()
        self.transport.assert_not_called()

    def test_remote_aliases_and_missing_local_metadata_fail_closed(self):
        self.local()
        bad = [
            {},
            {**SHOW, "remote_model": "secret-cloud"},
            {**SHOW, "remote_host": "https://ollama.com"},
            {**SHOW, "details": {"format": "gguf", "remote_host": "hidden"}},
            {**SHOW, "model_info": {}},
            {**SHOW, "details": {}},
            {**SHOW, "capabilities": ["completion"]},
            {**SHOW, "capabilities": "tools"},
        ]
        for show in bad:
            with self.subTest(show=show):
                self.transport.reset_mock()
                self.transport.side_effect = [show]
                with self.assertRaises(AppError):
                    self.complete()
                self.assertEqual(self.transport.call_count, 1)
                self.assertTrue(self.transport.call_args.args[1].endswith("/api/show"))

    def test_missing_model_does_not_pull_or_fallback(self):
        self.local()
        self.transport.side_effect = AppError("secret raw server response", status=404)
        with self.assertRaises(AppError) as caught:
            self.complete()
        self.assertEqual(caught.exception.status, 404)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self.transport.call_count, 1)
        self.store.get_secret.assert_not_called()

    def test_errors_are_sanitized_and_not_retried(self):
        for response in ({"error": "SECRET"}, "SECRET", ["SECRET"]):
            self.transport.return_value = response
            with self.assertRaises(AppError) as caught:
                self.complete()
            self.assertNotIn("SECRET", str(caught.exception))
        self.transport.reset_mock()
        self.transport.side_effect = AppError("SECRET", status=429, uncertain=True)
        with self.assertRaises(AppError) as caught:
            self.complete()
        self.assertEqual(caught.exception.status, 429)
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn("SECRET", str(caught.exception))
        self.transport.assert_called_once()

    def test_malformed_cloud_responses(self):
        call = {"type": "tool_use", "id": "x", "name": "read_mail", "input": {}}
        for content in (
            None,
            [],
            "text",
            [None],
            [{"type": "text", "text": 42}],
            [{**call, "input": "{}"}],
            [{**call, "input": []}],
            [{**call, "input": {"n": float("nan")}}],
            [{**call, "name": "execute"}],
            [{**call, "id": ""}],
            [call, call],
            [{"type": "unknown"}],
            [{**call, "id": str(i)} for i in range(13)],
        ):
            with self.subTest(content=content), self.assertRaises(AppError):
                self.transport.return_value = {"content": content}
                self.complete()

    def test_malformed_local_responses(self):
        self.local()
        for reply in (
            {},
            {**CHAT, "done": False},
            {"done": True, "message": []},
            {"done": True, "message": {"role": "user", "content": "hi"}},
            {"done": True, "message": {"role": "assistant", "content": 3}},
        ):
            with self.subTest(reply=reply), self.assertRaises(AppError):
                self.transport.side_effect = [SHOW, reply]
                self.complete()
        for calls in (
            None,
            {},
            [None],
            [{"function": None}],
            [{"function": {"name": "read_mail", "arguments": "{}"}}],
            [{"function": {"name": "read_mail", "arguments": []}}],
            [{"function": {"name": "execute", "arguments": {}}}],
            [{"function": {"name": "read_mail", "arguments": {}}}] * 13,
        ):
            with self.subTest(calls=calls), self.assertRaises(AppError):
                self.transport.side_effect = [
                    SHOW,
                    {"done": True, "message": {"role": "assistant", "content": "", "tool_calls": calls}},
                ]
                self.complete()

    def test_bounds(self):
        self.transport.return_value = {"content": [{"type": "text", "text": "x" * 262144}]}
        with self.assertRaises(AppError):
            self.complete()
        self.transport.return_value = {
            "content": [{"type": "tool_use", "name": "read_mail", "id": "x", "input": {"x": "x" * 32768}}]
        }
        with self.assertRaises(AppError):
            self.complete()
        self.transport.reset_mock()
        for tools in (
            TOOLS * 33,
            [{**TOOLS[0], "input_schema": {"type": "array"}}],
            [{**TOOLS[0], "description": "x" * 65536}],
        ):
            with self.assertRaises(AppError):
                self.complete(tools=tools)
        with self.assertRaises(AppError):
            self.complete(messages=[{"role": "user", "content": "x" * 1048576}])
        self.transport.assert_not_called()

    def test_bad_history_and_provider_rejected_without_network(self):
        for messages in (
            [],
            [{"role": "system", "content": "inject"}],
            [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "missing", "content": "oops"}]}],
            [{"role": "user", "content": [{"type": "image"}]}],
        ):
            with self.assertRaises(AppError):
                self.complete(messages)
        self.settings.provider = "fallback"
        with self.assertRaises(AppError):
            self.complete()
        self.transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
