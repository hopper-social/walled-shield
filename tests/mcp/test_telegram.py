import io
import pathlib
import sys
import threading
import time
import unittest
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.activity import ActivityStore
from mcp_approval_gate.gate import ApprovalGate, Request
from mcp_approval_gate.main import main
from mcp_approval_gate.telegram_approver import TelegramApprover, TelegramError, pair_alert_chat, pair_private_chat
from mcp_approval_gate.telegram_session import ApprovalSession, SESSION_SECONDS


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.sent = []
        self.fail_send = False

    def call(self, method, payload, timeout=12):
        with self.lock:
            self.calls.append((method, payload))
            if method == "sendMessage":
                if self.fail_send:
                    raise TelegramError("offline")
                message_id = len(self.sent) + 1
                self.sent.append((message_id, payload))
                return {"message_id": message_id}
        if method == "getUpdates":
            time.sleep(0.01)
            return []
        return True


def callback(message_id, nonce, decision="a", sender=123, chat=123):
    return {"id": "callback", "data": decision + ":" + nonce,
            "from": {"id": sender}, "message": {"message_id": message_id,
            "chat": {"id": chat, "type": "private"}}}


class TelegramTests(unittest.TestCase):
    def test_queued_pairout_is_applied_before_approvals_resume(self):
        class QueuedAPI(FakeAPI):
            def __init__(self):
                super().__init__()
                self.first = True

            def call(self, method, payload, timeout=12):
                if method == "getUpdates" and self.first:
                    self.first = False
                    return [{"update_id": index} for index in range(100)]
                if method == "getUpdates" and payload.get("offset") == 100:
                    return [{"update_id": 100, "message": {"text": "/pairout",
                            "chat": {"id": 123, "type": "private"},
                            "from": {"id": 123, "is_bot": False}}}]
                return super().call(method, payload, timeout)

        with TemporaryDirectory() as directory:
            session = ApprovalSession(pathlib.Path(directory) / "session.json", 123, 123)
            session.activate()
            remote = TelegramApprover("test", 123, 123, api=QueuedAPI(), session=session)
            remote.start()
            try:
                self.assertFalse(session.active())
                self.assertGreaterEqual(remote.offset, 101)
                self.assertFalse(remote.decide(Request("notes", "tools/call", {}, 1)))
            finally:
                remote.stop()

    def test_unbounded_telegram_backlog_fails_closed_before_start(self):
        class FullBacklog(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "getUpdates":
                    offset = payload["offset"]
                    return [{"update_id": offset + index} for index in range(100)]
                return super().call(method, payload, timeout)

        remote = TelegramApprover("test", 123, 123, api=FullBacklog())
        with self.assertRaises(TelegramError):
            remote.start()
        self.assertFalse(remote.healthy)
        self.assertIsNone(remote.worker)

    def approver(self):
        api = FakeAPI()
        remote = TelegramApprover("test-token", 123, 123, timeout=0.6, api=api)
        remote.healthy = True
        return remote, api

    def await_message(self, api, count=1):
        deadline = time.monotonic() + 2
        while len(api.sent) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(len(api.sent), count)
        return api.sent[count - 1]

    def test_outcome_alerts_are_separate_nonblocking_and_argument_free(self):
        api = FakeAPI()
        remote = TelegramApprover("test-token", 123, 123, api=api, alert_chat_id=-456)
        remote.start()
        try:
            message = {"jsonrpc": "2.0", "id": "private-id", "method": "tools/call",
                       "params": {"name": "notes_write", "arguments": {"text": "private body"}}}
            gate = ApprovalGate(lambda _: True, on_decision=remote.notify_decision)
            self.assertEqual(gate.evaluate(message, "notes"), (message, None))
            _, allowed = self.await_message(api)
            self.assertEqual(allowed["chat_id"], -456)
            self.assertIn("Action allowed", allowed["text"])
            self.assertNotIn("private body", allowed["text"])
            self.assertNotIn("private-id", allowed["text"])
            self.assertNotIn("reply_markup", allowed)
            blocked = ApprovalGate(lambda _: False, on_decision=remote.notify_decision)
            self.assertIsNone(blocked.evaluate(message, "notes")[0])
            _, alert = self.await_message(api, 2)
            self.assertIn("Action blocked", alert["text"])
            gate.evaluate({"id": 2, "method": "initialize"}, "notes")
            self.assertEqual(len(api.sent), 2)
            api.fail_send = True
            self.assertEqual(gate.evaluate(message, "notes"), (message, None))
        finally:
            remote.stop()

    def test_slow_alert_delivery_cannot_delay_decisions_or_grow_without_bound(self):
        sending = threading.Event()
        release = threading.Event()

        class SlowAPI(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "sendMessage":
                    sending.set()
                    release.wait(2)
                return super().call(method, payload, timeout)

        api = SlowAPI()
        remote = TelegramApprover("test", 123, 123, api=api, alert_chat_id=-456)
        remote.start()
        try:
            gate = ApprovalGate(lambda _: True, on_decision=remote.notify_decision)
            message = {"id": 1, "method": "tools/call", "params": {"name": "write"}}
            self.assertEqual(gate.evaluate(message, "notes"), (message, None))
            self.assertTrue(sending.wait(1))
            started = time.monotonic()
            for _ in range(70):
                self.assertEqual(gate.evaluate(message, "notes"), (message, None))
            self.assertLess(time.monotonic() - started, 1)
            self.assertLessEqual(remote.alerts.qsize(), 64)
        finally:
            release.set()
            remote.stop()

    def test_unpaired_outcome_alerts_do_not_enter_approval_chat(self):
        remote, api = self.approver()
        remote.notify_decision(Request("notes", "tools/call", {"name": "write"}, 1), False)
        self.assertEqual(api.sent, [])
        remote.stop()

    def test_only_paired_chat_and_exact_message_can_approve(self):
        remote, api = self.approver()
        request = Request("notes", "tools/call", {"name": "notes_write", "arguments": {"text": "private"}}, 77)
        result = []
        worker = threading.Thread(target=lambda: result.append(remote.decide(request)))
        worker.start()
        message_id, sent = self.await_message(api)
        self.assertNotIn("private", sent["text"])
        self.assertNotIn("77", sent["text"])
        self.assertEqual(sent["text"],
                         '[APPROVAL] Action waiting\n\nTool: "notes_write"\nServer: "notes"\n'
                         'Method: "tools/call"\nInputs: hidden\n\n'
                         'Allow sends the full request. Block stops it.\n'
                         'Expires in 0.6s. Claude/macOS permissions are separate.')
        nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
        remote._process(callback(message_id, nonce, sender=999))
        remote._process(callback(message_id, nonce, chat=999))
        remote._process(callback(message_id + 1, nonce))
        remote._process(callback(message_id, "f" * 32))
        self.assertFalse(result)
        remote._process(callback(message_id, nonce))
        worker.join(2)
        self.assertEqual(result, [True])
        remote._process(callback(message_id, nonce, decision="b"))
        self.assertEqual(result, [True])
        remote.stop()

    def test_concurrent_choices_do_not_cross_and_cancellation_denies(self):
        remote, api = self.approver()
        requests = [Request("notes", "tools/call", {"name": str(i)}, i) for i in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(remote.decide, req) for req in requests]
            self.await_message(api, 6)
            for message_id, sent in list(api.sent):
                name = sent["text"].split('Tool: "')[1].split('"')[0]
                nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
                remote._process(callback(message_id, nonce, "a" if int(name) % 2 == 0 else "b"))
            self.assertEqual([future.result(2) for future in futures], [True, False, True, False, True, False])
        remote.stop()
        cancelled = threading.Event()
        second, api = self.approver()
        req = Request("notes", "tools/call", {"name": "slow"}, 8, cancelled)
        worker = threading.Thread(target=lambda: result.append(second.decide(req)))
        result = []
        worker.start()
        message_id, sent = self.await_message(api)
        nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
        cancelled.set()
        second._process(callback(message_id, nonce))
        worker.join(2)
        self.assertEqual(result, [False])
        second.stop()

    def test_timeout_and_delivery_failure_fail_closed(self):
        remote, api = self.approver()
        remote.timeout = 0.03
        req = Request("notes", "tools/call", {"name": "write"}, 9)
        self.assertFalse(remote.decide(req))
        self.assertEqual(remote.pending, {})
        api.fail_send = True
        self.assertFalse(remote.decide(req))
        remote.stop()

    def test_send_does_not_hold_poll_lock_and_early_choice_matches_message(self):
        sending = threading.Event()
        release = threading.Event()
        class SlowAPI(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "sendMessage":
                    sending.set()
                    self.sent.append((1, payload))
                    release.wait(2)
                    return {"message_id": 1}
                return super().call(method, payload, timeout)
        api = SlowAPI()
        remote = TelegramApprover("test", 123, 123, api=api)
        remote.healthy = True
        message = {"jsonrpc": "2.0", "id": 44, "method": "tools/call",
                   "params": {"name": "notes_write", "arguments": {"text": "secret"}}}
        result = []
        worker = threading.Thread(target=lambda: result.append(ApprovalGate(remote.decide).evaluate(message, "notes")))
        worker.start()
        self.assertTrue(sending.wait(2))
        _, sent = self.await_message(api)
        nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
        callback_thread = threading.Thread(target=lambda: remote._process(callback(1, nonce)))
        callback_thread.start()
        callback_thread.join(0.5)
        self.assertFalse(callback_thread.is_alive(), "poll callbacks must not wait for the outbound network call")
        release.set()
        worker.join(2)
        self.assertEqual(result, [(message, None)])
        remote.stop()

    def test_poll_failure_denies_pending_even_after_recovery(self):
        remote, api = self.approver()
        request = Request("notes", "tools/call", {"name": "write"}, 9)
        result = []
        worker = threading.Thread(target=lambda: result.append(remote.decide(request)))
        worker.start()
        self.await_message(api)
        with remote.lock:
            remote.healthy = False
            for pending in remote.pending.values():
                pending.result = False
                pending.event.set()
        remote.healthy = True
        worker.join(2)
        self.assertEqual(result, [False])
        remote.stop()

    def test_pair_requires_matching_code_and_private_human(self):
        code = []
        class PairAPI(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "getUpdates" and payload.get("timeout") == 5:
                    key = code[0].split("/pair ")[1].split(" to your")[0]
                    return [
                        {"update_id": 1, "message": {"text": "/pair wrong", "chat": {"id": 1, "type": "private"}, "from": {"id": 1, "is_bot": False}}},
                        {"update_id": 2, "message": {"text": "/pair " + key, "chat": {"id": -10, "type": "group"}, "from": {"id": 5, "is_bot": False}}},
                        {"update_id": 3, "message": {"text": "/pair " + key, "chat": {"id": 123, "type": "private"}, "from": {"id": 123, "is_bot": False}}},
                    ]
                return super().call(method, payload, timeout)
        self.assertEqual(pair_private_chat("test", api=PairAPI(), announce=code.append), (123, 123))

    def test_permission_notice_is_once_per_paired_recipient_and_retries_failed_delivery(self):
        remote, api = self.approver()
        with TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "activity.sqlite3"
            history = ActivityStore(path)
            api.fail_send = True
            with self.assertRaises(TelegramError):
                remote.notify_permission_boundary(history)
            self.assertFalse(history.notice_sent("telegram-permissions", "123:123", 2))
            api.fail_send = False
            self.assertTrue(remote.notify_permission_boundary(history))
            _, sent = self.await_message(api)
            self.assertEqual(sent["chat_id"], 123)
            self.assertIn("[ALERT] Permission limits\n\n", sent["text"])
            self.assertIn("Claude sharing: decide in Claude.", sent["text"])
            self.assertIn("macOS access: decide in System Settings", sent["text"])
            self.assertIn("No permission request is pending", sent["text"])
            self.assertNotIn("reply_markup", sent)
            self.assertFalse(remote.notify_permission_boundary(history))
            history.close()
            reopened = ActivityStore(path)
            self.assertFalse(remote.notify_permission_boundary(reopened))
            another = TelegramApprover("test", 456, 456, api=api)
            another.healthy = True
            self.assertTrue(another.notify_permission_boundary(reopened))
            self.assertEqual(len(api.sent), 2)
            another.stop()
            reopened.close()
        remote.stop()

    def test_alert_pairing_requires_fresh_code_and_paired_group_sender(self):
        prompts = []
        class AlertPairAPI(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "getUpdates" and payload.get("timeout") == 5:
                    code = prompts[-1].split("/alertpair ")[1].split(" in your")[0]
                    return [
                        {"update_id": 1, "message": {"text": "/alertpair " + code,
                         "chat": {"id": -123, "type": "group"}, "from": {"id": 999, "is_bot": False}}},
                        {"update_id": 2, "message": {"text": "/alertpair " + code,
                         "chat": {"id": 123, "type": "private"}, "from": {"id": 123, "is_bot": False}}},
                        {"update_id": 3, "message": {"text": "/alertpair " + code,
                         "chat": {"id": -456, "type": "supergroup"}, "from": {"id": 123, "is_bot": False}}}]
                return super().call(method, payload, timeout)
        api = AlertPairAPI()
        self.assertEqual(pair_alert_chat("test", 123, api=api, announce=prompts.append), -456)
        self.assertEqual(api.sent[0][1]["chat_id"], -456)
        self.assertIn("[ALERT] Channel test passed", api.sent[0][1]["text"])
        self.assertIn("only after local configuration", api.sent[0][1]["text"])
        api.fail_send = True
        with self.assertRaises(TelegramError):
            pair_alert_chat("test", 123, api=api, announce=prompts.append)

    def test_channel_pairing_accepts_only_channel_posts(self):
        prompts = []
        class ChannelPairAPI(FakeAPI):
            def call(self, method, payload, timeout=12):
                if method == "getUpdates" and payload.get("timeout") == 5:
                    code = prompts[0].split("/alertpair ")[1].split(" in your")[0]
                    post = {"text": "/alertpair " + code, "chat": {"id": -222, "type": "channel"}}
                    return [{"update_id": 1, "message": post},
                            {"update_id": 2, "channel_post": post}]
                return super().call(method, payload, timeout)
        api = ChannelPairAPI()
        self.assertEqual(pair_alert_chat("test", 123, api=api, announce=prompts.append), -222)
        self.assertEqual(api.sent[0][1]["chat_id"], -222)

    def test_separate_alert_channel_never_receives_approval_actions(self):
        api = FakeAPI()
        remote = TelegramApprover("test", 123, 123, api=api, alert_chat_id=-456)
        remote.healthy = True
        with TemporaryDirectory() as directory:
            history = ActivityStore(pathlib.Path(directory) / "activity.sqlite3")
            self.assertTrue(remote.notify_permission_boundary(history))
            remote.notify_connector_ready()
            self.assertEqual([sent["chat_id"] for _, sent in api.sent], [-456, -456])
            self.assertTrue(all(sent["text"].startswith("[ALERT]") for _, sent in api.sent))
            self.assertTrue(all("reply_markup" not in sent for _, sent in api.sent))
            request = Request("notes", "tools/call", {"name": "notes_write"}, 7)
            result = []
            worker = threading.Thread(target=lambda: result.append(remote.decide(request)))
            worker.start()
            message_id, sent = self.await_message(api, 3)
            self.assertEqual(sent["chat_id"], 123)
            self.assertTrue(sent["text"].startswith("[APPROVAL]"))
            nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
            remote._process(callback(message_id, nonce, chat=-456))
            self.assertFalse(result)
            remote._process(callback(message_id, nonce, chat=123))
            worker.join(2)
            self.assertEqual(result, [True])
            self.assertFalse(remote.notify_permission_boundary(history))
            other = TelegramApprover("test", 123, 123, api=api, alert_chat_id=-789)
            other.healthy = True
            self.assertTrue(other.notify_permission_boundary(history))
            self.assertEqual(api.sent[-1][1]["chat_id"], -789)
            other.stop()
            history.close()
        remote.stop()

    def test_session_expires_after_24_hours_and_alerts_continue(self):
        with TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "telegram-session.json"
            session = ApprovalSession(path, 123, 123)
            with patch("mcp_approval_gate.telegram_session.time.time", return_value=1000000):
                session.activate()
                self.assertTrue(session.active())
            remote = TelegramApprover("test", 123, 123, api=FakeAPI(), session=session,
                                      alert_chat_id=-456)
            remote.healthy = True
            with patch("mcp_approval_gate.telegram_session.time.time", return_value=1000000 + SESSION_SECONDS):
                self.assertFalse(session.active())
                self.assertFalse(remote.decide(Request("notes", "tools/call", {"name": "write"}, 1)))
                remote.notify_connector_ready()
                self.assertEqual(remote.api.sent[-1][1]["chat_id"], -456)
            session.revoke()
            self.assertFalse(session.active())
            remote.stop()

    def test_expiry_while_request_is_pending_denies_late_allow(self):
        with TemporaryDirectory() as directory:
            session = ApprovalSession(pathlib.Path(directory) / "session.json", 123, 123)
            session.activate()
            api = FakeAPI()
            remote = TelegramApprover("test", 123, 123, api=api, session=session, timeout=2)
            remote.healthy = True
            result = []
            worker = threading.Thread(target=lambda: result.append(remote.decide(
                Request("notes", "tools/call", {"name": "write"}, 1))))
            worker.start()
            message_id, sent = self.await_message(api)
            nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
            with patch("mcp_approval_gate.telegram_session.time.time", return_value=10000000000):
                remote._process(callback(message_id, nonce))
                worker.join(2)
            self.assertEqual(result, [False])
            self.assertFalse(worker.is_alive())
            remote.stop()

    def test_session_creates_private_directory_for_first_pairing(self):
        with TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "private" / "telegram-session.json"
            session = ApprovalSession(path, 123, 123)
            session.activate()
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(session.active())

    def test_session_invalid_or_missing_state_fails_closed(self):
        with TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "session.json"
            session = ApprovalSession(path, 123, 123)
            self.assertFalse(session.active())
            session.activate()
            self.assertFalse(ApprovalSession(path, 999, 123).active())
            path.chmod(0o644)
            self.assertFalse(session.active())
            path.chmod(0o600)
            path.write_text("not-json")
            self.assertFalse(session.active())
            session.activate()
            path.unlink()
            path.symlink_to(pathlib.Path(directory) / "missing")
            self.assertFalse(session.active())

    def test_pairout_only_from_paired_private_chat_denies_pending_and_persists(self):
        with TemporaryDirectory() as directory:
            session = ApprovalSession(pathlib.Path(directory) / "session.json", 123, 123)
            session.activate()
            api = FakeAPI()
            remote = TelegramApprover("test", 123, 123, api=api, session=session,
                                      alert_chat_id=-456)
            remote.healthy = True
            result = []
            worker = threading.Thread(target=lambda: result.append(remote.decide(
                Request("notes", "tools/call", {"name": "write"}, 1))))
            worker.start()
            message_id, sent = self.await_message(api)
            nonce = sent["reply_markup"]["inline_keyboard"][0][1]["callback_data"][2:]
            command = {"text": "/pairout", "chat": {"id": 123, "type": "private"},
                       "from": {"id": 123, "is_bot": False}}
            remote._process_message({**command, "from": {"id": 999, "is_bot": False}})
            remote._process_message({**command, "chat": {"id": -456, "type": "channel"}})
            self.assertTrue(session.active())
            remote._process_message(command)
            remote._process(callback(message_id, nonce))
            worker.join(2)
            self.assertEqual(result, [False])
            self.assertFalse(ApprovalSession(session.path, 123, 123).active())
            remote.notify_connector_ready()
            self.assertEqual(api.sent[-1][1]["chat_id"], -456)
            remote.stop()

    def test_alert_channel_rejects_invalid_destinations(self):
        for chat in (123, 0, True, "-456"):
            with self.assertRaises(ValueError):
                TelegramApprover("test", 123, 123, alert_chat_id=chat)

    def test_connector_ready_alert_is_informational_and_failures_do_not_claim_delivery(self):
        remote, api = self.approver()
        remote.notify_connector_ready()
        _, sent = self.await_message(api)
        self.assertEqual(sent["chat_id"], 123)
        self.assertEqual(sent["text"], "[ALERT] Connector ready\n\n"
                         "Guarded MCP handshake completed. No action is pending.\n"
                         "This does not confirm Claude restarted.")
        self.assertNotIn("reply_markup", sent)
        self.assertTrue(sent["protect_content"])
        api.fail_send = True
        with self.assertRaises(TelegramError):
            remote.notify_connector_ready()
        self.assertEqual(len(api.sent), 1)
        remote.stop()
        with self.assertRaises(TelegramError):
            remote.notify_connector_ready()

    def test_permission_guide_is_explicit_not_an_os_grant(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertEqual(main(["permissions"]), 0)
        self.assertIn("Screen Recording", output.getvalue())
        self.assertIn("cannot grant", output.getvalue())


if __name__ == "__main__":
    unittest.main()
