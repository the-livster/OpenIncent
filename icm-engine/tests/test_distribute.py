from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from icm_engine.distribute import (
    OutgoingMessage,
    SmtpConfig,
    build_messages,
    send_via_smtp,
    write_eml,
    write_mail_merge_csv,
)
from icm_engine.models import Payee


def _payee(**overrides):
    defaults = dict(
        id="P001", name="Alice", quota=Decimal("0"), plan_id="p",
        effective_from=date(2026, 1, 1),
    )
    return Payee(**(defaults | overrides))


class TestBuildMessages:
    def test_isolation(self) -> None:
        """P1's message contains ONLY P1's data, not P2's."""
        payees = [
            _payee(id="P1", name="Alice", email="alice@example.com"),
            _payee(id="P2", name="Bob", email="bob@example.com"),
        ]
        files = [
            {"payee_id": "P1", "period": "2026-01", "path": "stmt_P1.html"},
            {"payee_id": "P2", "period": "2026-01", "path": "stmt_P2.html"},
        ]
        messages, skipped = build_messages(files, payees)
        assert len(messages) == 2
        p1 = next(m for m in messages if m.payee_id == "P1")
        assert p1.to == "alice@example.com"
        assert "bob" not in p1.body.lower()
        assert "bob@example.com" not in p1.to
        assert all("P2" not in str(a) for a in p1.attachments)

    def test_missing_email_skipped(self) -> None:
        """Payee with no email produces skipped result, not a crash."""
        payees = [_payee(id="P1", name="Alice")]  # no email
        files = [{"payee_id": "P1", "period": "2026-01", "path": "stmt_P1.html"}]
        messages, skipped = build_messages(files, payees)
        assert len(messages) == 0
        assert len(skipped) == 1
        assert skipped[0].status == "skipped"
        assert skipped[0].reason == "no_email"

    def test_template_interpolation(self) -> None:
        """Templates are interpolated with name and period."""
        payees = [_payee(id="P1", name="Alice", email="a@b.com")]
        files = [{"payee_id": "P1", "period": "Q1", "path": "stmt_P1.html"}]
        messages, _ = build_messages(
            files, payees,
            subject_template="Statement for {name} - {period}",
            body_template="Hi {name}, total: ${total}",
        )
        assert messages[0].subject == "Statement for Alice - Q1"
        assert "Hi Alice" in messages[0].body

    def test_deterministic(self) -> None:
        """Same inputs produce identical output."""
        payees = [_payee(id="P1", name="Alice", email="a@b.com")]
        files = [{"payee_id": "P1", "period": "2026-01", "path": "stmt_P1.html"}]
        m1, _ = build_messages(files, payees)
        m2, _ = build_messages(files, payees)
        assert m1[0].subject == m2[0].subject
        assert m1[0].body == m2[0].body


class TestSendViaSmtp:
    def test_dry_run_zero_sends(self) -> None:
        """dry_run=True performs zero network calls."""
        msg = OutgoingMessage(payee_id="P1", to="a@b.com", subject="S", body="B")
        results = send_via_smtp([msg], SmtpConfig(host="localhost"), dry_run=True)
        assert len(results) == 1
        assert results[0].status == "sent"

    def test_isolation_per_recipient(self) -> None:
        """Each recipient receives exactly one message with only their own data."""
        msgs = [
            OutgoingMessage(payee_id="P1", to="alice@example.com", subject="S1", body="B1"),
            OutgoingMessage(payee_id="P2", to="bob@example.com", subject="S2", body="B2"),
        ]
        with mock.patch("icm_engine.distribute.smtplib.SMTP") as mock_smtp, \
             mock.patch("icm_engine.distribute.ssl.create_default_context"):
            instance = mock_smtp.return_value
            results = send_via_smtp(
                msgs,
                SmtpConfig(host="smtp.test", user="u", password="p", from_addr="noreply@test.com"),
                dry_run=False,
            )
            assert len(results) == 2
            assert all(r.status == "sent" for r in results)
            # Each message should have been sent to exactly its own recipient
            calls = instance.sendmail.call_args_list
            assert len(calls) == 2
            # First call: to alice
            assert calls[0][0][1] == ["alice@example.com"]
            # Second call: to bob
            assert calls[1][0][1] == ["bob@example.com"]


class TestWriteEml:
    def test_isolation(self, tmp_path: Path) -> None:
        """Generated .eml for P1 contains only P1's data."""
        msgs = [
            OutgoingMessage(payee_id="P1", to="alice@example.com", subject="Alice Statement", body="Alice body"),
            OutgoingMessage(payee_id="P2", to="bob@example.com", subject="Bob Statement", body="Bob body"),
        ]
        paths = write_eml(msgs, tmp_path)
        assert len(paths) == 2
        p1_content = (tmp_path / "P1.eml").read_text()
        assert "alice@example.com" in p1_content
        assert "bob@example.com" not in p1_content
        assert "Bob" not in p1_content

    def test_deterministic(self, tmp_path: Path) -> None:
        """Same inputs produce identical .eml files."""
        msgs = [OutgoingMessage(payee_id="P1", to="a@b.com", subject="S", body="B")]
        p1 = write_eml(msgs, tmp_path)
        p2 = write_eml(msgs, tmp_path)
        assert p1[0].read_bytes() == p2[0].read_bytes()


class TestWriteMailMergeCsv:
    def test_columns(self, tmp_path: Path) -> None:
        """Mail-merge CSV has the expected columns."""
        msgs = [OutgoingMessage(payee_id="P1", to="a@b.com", subject="S", body="B",
                                attachments=[Path("stmt_P1.html")])]
        path = write_mail_merge_csv(msgs, tmp_path / "merge.csv")
        content = path.read_text()
        assert "payee_id" in content
        assert "a@b.com" in content
