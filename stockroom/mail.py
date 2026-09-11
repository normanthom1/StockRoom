from django.core.mail.backends.console import EmailBackend


class ReadableConsoleBackend(EmailBackend):
    """Console email as plain text instead of MIME.

    With no email provider (#13), production "sends" mail into Railway's logs.
    The stock console backend prints quoted-printable MIME, which wraps long
    lines with '=' breaks and splits a reset link in two. Print the body as is.
    """

    def write_message(self, message):
        self.stream.write(f"To: {', '.join(message.to)}\nSubject: {message.subject}\n\n{message.body}\n")
        self.stream.write("-" * 79 + "\n")
