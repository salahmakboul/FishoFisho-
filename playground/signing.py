"""Signed, time-limited download URLs for MessageAttachment, built on
Django's stdlib `django.core.signing` (no new dependency). See the
ATTACHMENTS_ROOT comment in fishofisho/settings.py for the full rationale —
short version: message attachments can belong to private-room messages, so
"anyone with the URL" isn't an acceptable access model the way it was under
the old blanket static(MEDIA_URL, ...) serving. This gives the same shape as
an S3 pre-signed URL (time-limited + tamper-proof) without needing any cloud
storage credentials.

The signature alone is NOT sufficient to download a file — see
AttachmentDownloadView, which also requires the requester to be logged in
AND a member of the room the attachment's message belongs to. That's a
deliberate departure from "real" S3 signed URLs, which are often handed out
specifically to allow temporary *unauthenticated* access (e.g. so an <img>
tag can load one with no extra headers). Here, since the frontend is
same-origin, the session cookie already rides along with a plain <img src>
request for free, so there's no real downside to requiring auth too — it's
defense in depth, not a missing feature.
"""

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired

# How long a signed attachment download link stays valid for. Short enough
# that a leaked/logged URL (e.g. in a proxy access log) is a narrow window,
# long enough that a chat session someone has open for a while can still
# load images without every single one needing an on-demand refresh.
ATTACHMENT_URL_MAX_AGE_SECONDS = 10 * 60  # 10 minutes

_SALT = "playground.attachment-download"


def _signer():
    return signing.TimestampSigner(salt=_SALT)


def sign_attachment_url(attachment_id) -> str:
    """Return a signed, timestamped token for `attachment_id`. The token
    embeds the id itself (not just a bare signature), so a token minted for
    one attachment can't be replayed against a different attachment id."""
    return _signer().sign(str(attachment_id))


def verify_attachment_signature(attachment_id, token: str) -> bool:
    """True if `token` is a valid, unexpired signature for `attachment_id`.
    Catches both tampering (BadSignature) and expiry (SignatureExpired) —
    callers don't need to distinguish the two, both mean "reject"."""
    if not token:
        return False
    try:
        value = _signer().unsign(token, max_age=ATTACHMENT_URL_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return value == str(attachment_id)
