from unittest.mock import MagicMock, patch

from adapters.notifications.email import EmailConfig, is_configured, load_email_config, send_email


def test_load_email_config_from_env():
    config = load_email_config({
        "GMAIL_ADDRESS": "iv@example.com", "GMAIL_APP_PASSWORD": "secret", "NOTIFY_EMAIL": "owner@example.com",
    })
    assert config == EmailConfig(address="iv@example.com", app_password="secret", notify_to="owner@example.com")


def test_load_email_config_defaults_notify_to_address():
    config = load_email_config({"GMAIL_ADDRESS": "iv@example.com", "GMAIL_APP_PASSWORD": "secret"})
    assert config.notify_to == "iv@example.com"


def test_is_configured_false_when_incomplete():
    assert is_configured(EmailConfig()) is False
    assert is_configured(EmailConfig(address="a@example.com")) is False


def test_is_configured_true_when_complete():
    assert is_configured(EmailConfig(address="a", app_password="b", notify_to="c")) is True


def test_send_email_no_ops_when_not_configured():
    with patch("smtplib.SMTP_SSL") as smtp_cls:
        send_email(config=EmailConfig(), subject="test", html_body="<p>hi</p>")
        smtp_cls.assert_not_called()


def test_send_email_sends_via_smtp_when_configured():
    config = EmailConfig(address="iv@example.com", app_password="secret", notify_to="owner@example.com")

    with patch("smtplib.SMTP_SSL") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server

        send_email(config=config, subject="iV Sleep Cycle", html_body="<p>narrative</p>")

        server.login.assert_called_once_with("iv@example.com", "secret")
        sent_message = server.send_message.call_args.args[0]
        assert sent_message["Subject"] == "iV Sleep Cycle"
        assert sent_message["To"] == "owner@example.com"
