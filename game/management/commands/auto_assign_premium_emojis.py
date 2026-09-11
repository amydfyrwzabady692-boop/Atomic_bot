"""Auto-assign Telegram Premium custom-emoji icons to buttons and text from discovered packs.

Usage:
    python manage.py auto_assign_premium_emojis
    python manage.py auto_assign_premium_emojis --force
"""

from django.core.management.base import BaseCommand
from game.emoji_sync import sync_all_emojis_from_packs


class Command(BaseCommand):
    help = "Discover Telegram Premium sticker packs and auto-assign custom emojis to buttons and text."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reassign all buttons and text keys, overwriting existing overrides.",
        )
        parser.add_argument(
            "--token",
            type=str,
            default=None,
            help="Optional Telegram Bot token to use.",
        )

    def handle(self, *args, **options):
        force = options.get("force", False)
        token = options.get("token")

        self.stdout.write(self.style.NOTICE("Starting Telegram Premium emoji pack discovery and synchronization..."))
        res = sync_all_emojis_from_packs(force=force, bot_token=token)

        if not res.get("success"):
            self.stderr.write(self.style.ERROR(f"Sync failed: {res.get('error')}"))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Successfully synced from {res['sets']} sticker set(s) ({res['total_emojis_in_packs']} emojis total):\n"
            f"  - Buttons: {res['buttons_assigned']} assigned, {res['buttons_skipped']} skipped (already set)\n"
            f"  - Text:    {res['text_assigned']} assigned, {res['text_skipped']} skipped (already set)\n"
            f"  - Glyphs:  {res['glyphs_assigned']} literal emoji theme(s) active\n"
            f"  - Appearance table: {res['appearance_synced']} rows updated\n"
        ))
        if res.get("buttons_unmatched"):
            self.stdout.write(self.style.WARNING(f"Unmatched buttons: {res['buttons_unmatched']}"))
        if res.get("text_unmatched"):
            self.stdout.write(self.style.WARNING(f"Unmatched text keys: {res['text_unmatched']}"))
