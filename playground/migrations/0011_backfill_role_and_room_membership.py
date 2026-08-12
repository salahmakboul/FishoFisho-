from django.db import migrations


def backfill_roles_and_memberships(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("playground", "UserProfile")
    Room = apps.get_model("playground", "Room")
    RoomMembership = apps.get_model("playground", "RoomMembership")

    # Superusers -> owner (so the app never ends up with zero owners on an
    # existing dev/prod DB that already has real users); everyone else ->
    # member (the field's own default, set explicitly here for clarity).
    for user in User.objects.all():
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = "owner" if user.is_superuser else "member"
        profile.save()

    # If literally no superuser exists yet, promote the earliest-created
    # user to owner so there's always at least one owner in the workspace.
    if not UserProfile.objects.filter(role="owner").exists():
        first_user = User.objects.order_by("date_joined", "id").first()
        if first_user is not None:
            profile, _ = UserProfile.objects.get_or_create(user=first_user)
            profile.role = "owner"
            profile.save()

    # Carry the legacy `participants` M2M over into RoomMembership so
    # existing rooms' membership data isn't silently lost now that
    # RoomMembership is the real source of truth. The room host also
    # becomes an admin member of their own room if not already a member.
    for room in Room.objects.all().prefetch_related("participants"):
        for user in room.participants.all():
            RoomMembership.objects.get_or_create(room=room, user=user)
        if room.host_id is not None:
            RoomMembership.objects.get_or_create(
                room=room, user_id=room.host_id, defaults={"role": "admin"}
            )


def noop_reverse(apps, schema_editor):
    # Data migrations are one-directional here — reversing would mean
    # guessing which roles/memberships were "original", which isn't
    # recoverable. Left as a no-op so `migrate` can still step backwards
    # through the schema changes if ever needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("playground", "0010_room_is_private_userprofile_role_invitation_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_roles_and_memberships, noop_reverse),
    ]
