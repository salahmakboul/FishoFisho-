# Best-effort backfill of ConversationRead from the about-to-be-removed
# PrivateMessage.receiver/is_read fields, run *before* 0018 drops them.
#
# Limitations (documented rather than over-engineered around, per the
# group-DM slice's read-tracking design):
#   - Only conversations with exactly 2 participants have an unambiguous
#     "receiver" to backfill for — get_other_user()/the old `receiver` FK
#     was itself only ever meaningful for exactly 2 participants, and no
#     group (3+) conversations exist yet in this dataset at the time this
#     model was introduced, so this covers 100% of pre-existing data.
#   - Read state is approximated to the timestamp of the newest is_read=True
#     message addressed to that receiver in that conversation. A receiver
#     who had read a later message that happened to still have is_read=False
#     (shouldn't occur given the old mark-as-read-on-view behavior, but
#     isn't guaranteed by a DB constraint) would be under-credited, not
#     over-credited — the safe direction for an approximation like this.
#   - Senders don't get a ConversationRead row seeded for their own sent
#     messages: unread_count_for() already excludes a user's own messages
#     from their own unread count, so no seeding is needed there.
from django.db import migrations
from django.db.models import Max


def backfill(apps, schema_editor):
    PrivateConversation = apps.get_model('playground', 'PrivateConversation')
    ConversationRead = apps.get_model('playground', 'ConversationRead')

    for conversation in PrivateConversation.objects.prefetch_related('participants').all():
        participant_ids = list(conversation.participants.values_list('id', flat=True))
        if len(participant_ids) != 2:
            continue
        for user_id in participant_ids:
            latest_read = (
                conversation.messages.filter(receiver_id=user_id, is_read=True)
                .aggregate(latest=Max('created_at'))
                .get('latest')
            )
            if latest_read is None:
                continue
            ConversationRead.objects.update_or_create(
                conversation=conversation,
                user_id=user_id,
                defaults={'last_read_at': latest_read},
            )


def noop_reverse(apps, schema_editor):
    # Nothing to undo — ConversationRead rows are harmless to leave behind
    # even if this migration is reversed (0018 removing receiver/is_read
    # would need to run in reverse first anyway to restore that data).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('playground', '0016_conversationread_presence'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
