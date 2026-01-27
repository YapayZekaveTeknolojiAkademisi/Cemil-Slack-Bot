#!/usr/bin/env python3
"""
Challenge Management CLI Tool for Cemil Bot
Manage challenges, update statuses, and fix user states directly from the terminal.
"""

import os
import sys
import argparse
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
except ImportError:
    print("❌ Error: 'rich' library is missing. Please install it: pip install rich")
    sys.exit(1)

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:
    print("❌ Error: 'slack_sdk' library is missing. Please install it: pip install slack_sdk")
    sys.exit(1)

from src.core.settings import get_settings

console = Console()

class ChallengeManager:
    def __init__(self):
        self.settings = get_settings()
        self.db_path = self.settings.database_path
        
        if not os.path.exists(self.db_path):
            console.print(f"[bold red]❌ Database not found at:[/bold red] {self.db_path}")
            sys.exit(1)
            
        # Slack Client (for channel analysis)
        self.slack_client = WebClient(token=self.settings.slack_bot_token)
        self.user_client = WebClient(token=self.settings.slack_user_token) if self.settings.slack_user_token else None

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_challenges(self, status: Optional[str] = None, limit: int = 20):
        """List challenges with optional status filter."""
        conn = self.get_connection()
        cursor = conn.cursor()

        query = "SELECT * FROM challenge_hubs"
        params = []
        
        if status and status.lower() != 'all':
            query += " WHERE status = ?"
            params.append(status.lower())
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            console.print("[yellow]⚠️ No challenges found matches your criteria.[/yellow]")
            return

        table = Table(title=f"🏆 Challenge List ({status if status else 'All'})")
        
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status", style="magenta")
        table.add_column("Theme", style="green")
        table.add_column("Creator", style="blue")
        table.add_column("Team", justify="right")
        table.add_column("Created At", style="dim")

        for row in rows:
            # Format status color
            status_style = "white"
            s = row['status']
            if s == 'active': status_style = "bold green"
            elif s == 'recruiting': status_style = "bold yellow"
            elif s == 'completed': status_style = "bold blue"
            elif s == 'evaluating': status_style = "bold purple"
            elif s == 'failed': status_style = "red"

            # Count participants
            conn2 = self.get_connection()
            cur2 = conn2.cursor()
            cur2.execute("SELECT COUNT(*) as count FROM challenge_participants WHERE challenge_hub_id = ?", (row['id'],))
            p_count = cur2.fetchone()['count']
            conn2.close()
            
            created_at = row['created_at'][:16] if row['created_at'] else "N/A"

            table.add_row(
                row['id'][:8],
                f"[{status_style}]{s.upper()}[/{status_style}]",
                row['theme'] or "TBD",
                row['creator_id'],
                f"{p_count}/{row['team_size'] or 0}",
                created_at
            )

        console.print(table)

    def get_challenge_info(self, challenge_id: str):
        """Show detailed info for a specific challenge."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Try to find by short ID first (startswith)
        if len(challenge_id) < 30:
            cursor.execute("SELECT * FROM challenge_hubs WHERE id LIKE ?", (f"{challenge_id}%",))
        else:
            cursor.execute("SELECT * FROM challenge_hubs WHERE id = ?", (challenge_id,))
            
        challenge = cursor.fetchone()
        
        if not challenge:
            console.print(f"[bold red]❌ Challenge not found: {challenge_id}[/bold red]")
            conn.close()
            return

        full_id = challenge['id']
        
        # Get Participants
        cursor.execute("SELECT * FROM challenge_participants WHERE challenge_hub_id = ?", (full_id,))
        participants = cursor.fetchall()
        
        conn.close()

        # Display
        console.print(Panel(f"[bold cyan]🔍 Challenge Details: {full_id}[/bold cyan]"))
        
        rprint(f"📌 [bold]Theme:[/bold] {challenge['theme']}")
        rprint(f"📊 [bold]Status:[/bold] {challenge['status'].upper()}")
        rprint(f"👤 [bold]Creator:[/bold] {challenge['creator_id']}")
        rprint(f"📅 [bold]Created:[/bold] {challenge['created_at']}")
        rprint(f"🏁 [bold]Deadline:[/bold] {challenge['deadline'] or 'N/A'}")
        rprint(f"📢 [bold]Channel:[/bold] {challenge['challenge_channel_id'] or 'N/A'}")
        
        rprint("\n👥 [bold]Participants:[/bold]")
        if participants:
            for p in participants:
                rprint(f"  - {p['user_id']} ({p['role']})")
        else:
            rprint("  [dim]No participants yet.[/dim]")

    def update_status(self, challenge_id: str, new_status: str):
        """Force update the status of a challenge."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Valid statuses
        valid_statuses = ['recruiting', 'active', 'evaluating', 'completed', 'failed', 'cancelled']
        if new_status.lower() not in valid_statuses:
            console.print(f"[bold red]❌ Invalid status. Choose from: {', '.join(valid_statuses)}[/bold red]")
            conn.close()
            return

        # Handle Short ID
        target_id = challenge_id
        if len(challenge_id) < 30:
             cursor.execute("SELECT id FROM challenge_hubs WHERE id LIKE ?", (f"{challenge_id}%",))
             row = cursor.fetchone()
             if row:
                 target_id = row['id']
             else:
                 console.print(f"[bold red]❌ Challenge not found: {challenge_id}[/bold red]")
                 conn.close()
                 return

        try:
            cursor.execute(
                "UPDATE challenge_hubs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                (new_status.lower(), target_id)
            )
            conn.commit()
            
            if cursor.rowcount > 0:
                console.print(f"[bold green]✅ Success! Challenge {target_id[:8]} status updated to '{new_status.upper()}'[/bold green]")
            else:
                 console.print(f"[bold red]❌ Challenge not found or update failed.[/bold red]")
                 
        except Exception as e:
            console.print(f"[bold red]❌ Database error: {e}[/bold red]")
        finally:
            conn.close()

    def delete_challenge(self, challenge_id: str, confirm: bool = False):
        """Delete a challenge and all related data."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Handle Short ID
        target_id = challenge_id
        theme = "Unknown"
        
        if len(challenge_id) < 30:
             cursor.execute("SELECT id, theme FROM challenge_hubs WHERE id LIKE ?", (f"{challenge_id}%",))
             row = cursor.fetchone()
             if row:
                 target_id = row['id']
                 theme = row['theme']
             else:
                 console.print(f"[bold red]❌ Challenge not found: {challenge_id}[/bold red]")
                 conn.close()
                 return
        else:
            cursor.execute("SELECT theme FROM challenge_hubs WHERE id = ?", (target_id,))
            row = cursor.fetchone()
            if row: theme = row['theme']

        if not confirm:
            console.print(f"[bold red]⚠️  WARNING: You are about to DELETE challenge {target_id[:8]} ({theme})[/bold red]")
            val = input("Type 'yes' to confirm: ")
            if val.lower() != 'yes':
                print("Operation cancelled.")
                conn.close()
                return

        try:
            # Get existing record for backup
            cursor.execute("SELECT * FROM challenge_hubs WHERE id = ?", (target_id,))
            hub_row = cursor.fetchone()
            
            if hub_row:
                # Store a mini-backup in logs/deleted_challenges.json for safety
                import json
                log_dir = "logs"
                if not os.path.exists(log_dir): os.makedirs(log_dir)
                
                backup_data = {
                    "deleted_at": datetime.now().isoformat(),
                    "hub": dict(hub_row)
                }
                
                with open(os.path.join(log_dir, "deleted_challenges.log"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(backup_data, ensure_ascii=False) + "\n")

            # Delete children first
            cursor.execute("DELETE FROM challenge_participants WHERE challenge_hub_id = ?", (target_id,))
            cursor.execute("DELETE FROM challenge_evaluators WHERE evaluation_id IN (SELECT id FROM challenge_evaluations WHERE challenge_hub_id = ?)", (target_id,))
            cursor.execute("DELETE FROM challenge_evaluations WHERE challenge_hub_id = ?", (target_id,))
            
            # Delete parent
            cursor.execute("DELETE FROM challenge_hubs WHERE id = ?", (target_id,))
            conn.commit()
            
            console.print(f"[bold green]✅ Challenge deleted successfully! (A safety backup was saved to logs/deleted_challenges.log)[/bold green]")

        except Exception as e:
            console.print(f"[bold red]❌ Error: {e}[/bold red]")
        finally:
            conn.close()

    def reset_user(self, user_id: str):
        """
        Reset a user's active status.
        Removes them from any 'active' or 'recruiting' challenges so they can start fresh.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        console.print(f"[yellow]🔍 Checking active challenges for user: {user_id}...[/yellow]")
        
        # 1. As Participant
        cursor.execute("""
            SELECT ch.id, ch.status FROM challenge_hubs ch
            JOIN challenge_participants cp ON ch.id = cp.challenge_hub_id
            WHERE cp.user_id = ? AND ch.status IN ('recruiting', 'active')
        """, (user_id,))
        
        rows = cursor.fetchall()
        
        if rows:
            for row in rows:
                console.print(f"   found as participant in: {row['id'][:8]} ({row['status']})")
                # Remove participant record
                cursor.execute("DELETE FROM challenge_participants WHERE challenge_hub_id = ? AND user_id = ?", (row['id'], user_id))
                console.print(f"   [green]Removed from participant list.[/green]")
        
        # 2. As Creator
        cursor.execute("""
            SELECT id, status FROM challenge_hubs 
            WHERE creator_id = ? AND status IN ('recruiting', 'active')
        """, (user_id,))
        
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                console.print(f"   found as creator of: {row['id'][:8]} ({row['status']})")
                # Force status to cancelled
                cursor.execute("UPDATE challenge_hubs SET status = 'cancelled' WHERE id = ?", (row['id'],))
                console.print(f"   [green]Challenge cancelled.[/green]")

        conn.commit()
        conn.close()
        console.print(f"[bold green]✅ User {user_id} has been reset. They can now start/join new challenges.[/bold green]")

    def check_stuck_users(self):
        """Find users who might be stuck in 'active' challenges for too long."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        console.print("[yellow]🔍 Scanning for potentially stuck users (> 72 hours)...[/yellow]")
        
        # 72 hours ago
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=72)).isoformat()
        
        query = """
            SELECT ch.id, ch.status, ch.created_at, ch.creator_id, cp.user_id 
            FROM challenge_hubs ch
            LEFT JOIN challenge_participants cp ON ch.id = cp.challenge_hub_id
            WHERE ch.status IN ('recruiting', 'active') 
            AND ch.created_at < ?
        """
        
        cursor.execute(query, (cutoff,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            console.print("[green]✅ No stuck users found. Everyone is within limits.[/green]")
            return

        table = Table(title="⚠️  Potentially Stuck Users (Older than 72h)")
        table.add_column("Challenge ID", style="cyan")
        table.add_column("Status", style="bold yellow")
        table.add_column("Created At", style="dim")
        table.add_column("Stuck User", style="bold red")
        table.add_column("Role", style="blue")
        
        stuck_users = set()

        for row in rows:
            # Creator
            if row['creator_id']:
                table.add_row(
                    row['id'][:8], row['status'], row['created_at'][:16], row['creator_id'], "Creator"
                )
                stuck_users.add(row['creator_id'])
            
            # Participant
            if row['user_id']:
                table.add_row(
                    row['id'][:8], row['status'], row['created_at'][:16], row['user_id'], "Participant"
                )
                stuck_users.add(row['user_id'])
                
        console.print(table)
        
        if stuck_users:
            console.print(f"\n[bold]Found {len(stuck_users)} unique stuck users.[/bold]")
            if input("👉 Do you want to reset a user now? (y/n): ").lower() == 'y':
                uid = input("Enter User ID to reset: ")
                self.reset_user(uid)

    def import_channel(self, channel_id: str):
        """Analyze a Slack channel and import it as a challenge hub."""
        console.print(f"[yellow]🔍 Analyzing channel: {channel_id}...[/yellow]")
        
        try:
            # 1. Fetch Channel Info
            ch_info = self.slack_client.conversations_info(channel=channel_id)
            channel = ch_info["channel"]
            channel_name = channel.get("name", "N/A")
            created_ts = channel.get("created", 0)
            created_at = datetime.fromtimestamp(created_ts).isoformat()
            
            # 2. Fetch Members
            members_resp = self.slack_client.conversations_members(channel=channel_id)
            members = members_resp.get("members", [])
            bot_id = self.slack_client.auth_test()["user_id"]
            human_members = [m for m in members if m != bot_id]
            
            console.print(Panel(f"[bold green]✅ Channel Found: #{channel_name}[/bold green]\n"
                              f"📅 Created: {created_at}\n"
                              f"👥 Members: {len(human_members)} humans"))

            # 3. Guess Creator (Oldest message or channel creator)
            creator_id = channel.get("creator")
            
            # 4. Fetch Themes/Projects for Selection
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM challenge_themes")
            themes = cursor.fetchall()
            
            if not themes:
                console.print("[red]❌ No themes found in database. Cannot create challenge.[/red]")
                conn.close()
                return

            console.print("\n[bold]Select Theme:[/bold]")
            for i, t in enumerate(themes, 1):
                console.print(f"[{i}] {t['name']}")
            
            theme_idx = int(input("\n👉 Choice: ") or "1") - 1
            selected_theme = themes[theme_idx]['name']
            
            # 5. List Projects in Theme
            cursor.execute("SELECT id, name FROM challenge_projects WHERE theme = ?", (selected_theme,))
            projects = cursor.fetchall()
            selected_project_id = None
            if projects:
                console.print(f"\n[bold]Select Project for {selected_theme}:[/bold]")
                for i, p in enumerate(projects, 1):
                    console.print(f"[{i}] {p['name']}")
                p_idx = int(input("\n👉 Choice (or Enter for None): ") or "0") - 1
                if p_idx >= 0:
                    selected_project_id = projects[p_idx]['id']
            
            # 6. Final Confirmation
            console.print(f"\n[bold cyan]Import Plan:[/bold cyan]")
            console.print(f"- Theme: {selected_theme}")
            console.print(f"- Project: {selected_project_id or 'TBD'}")
            console.print(f"- Creator: {creator_id}")
            console.print(f"- Participants: {', '.join(human_members)}")
            
            confirm = input("\n🚀 Proceed with import? (y/n): ")
            if confirm.lower() != 'y':
                console.print("[yellow]Import cancelled.[/yellow]")
                conn.close()
                return

            # 7. Insert Hub
            import uuid
            hub_id = str(uuid.uuid4())
            
            # Try to get default values for new columns
            hub_channel_id = self.settings.startup_channel or "C00000000" # fallback
            
            cursor.execute("""
                INSERT INTO challenge_hubs (
                    id, creator_id, theme, team_size, status, 
                    challenge_channel_id, hub_channel_id, selected_project_id, 
                    created_at, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hub_id, creator_id, selected_theme, len(human_members), 'active', 
                channel_id, hub_channel_id, selected_project_id, 
                created_at, created_at
            ))
            
            # 8. Insert Participants
            for m_id in human_members:
                cursor.execute("""
                    INSERT OR IGNORE INTO challenge_participants (id, challenge_hub_id, user_id, role)
                    VALUES (?, ?, ?, ?)
                """, (str(uuid.uuid4()), hub_id, m_id, 'member' if m_id != creator_id else 'lead'))
                
            conn.commit()
            conn.close()
            console.print(f"[bold green]✅ Success! Channel imported as challenge ID: {hub_id[:8]}[/bold green]")
            
        except SlackApiError as e:
            console.print(f"[bold red]❌ Slack API Error: {e.response['error']}[/bold red]")
        except Exception as e:
            console.print(f"[bold red]❌ Error: {e}[/bold red]")

    def manual_create_challenge(self):
        """Manually create or restore a challenge hub record."""
        console.print(Panel("[bold yellow]🛠️  Manual Challenge Entry / Restore[/bold yellow]"))
        
        try:
            import uuid
            
            hub_id = input("Enter Challenge ID (Leave empty for new UUID): ").strip()
            if not hub_id: hub_id = str(uuid.uuid4())
            
            channel_id = input("Enter Slack Channel ID (C...): ").strip()
            theme = input("Enter Theme Name (e.g. AI Chatbot): ").strip()
            creator_id = input("Enter Creator Slack ID (U...): ").strip()
            participants_raw = input("Enter Participant IDs (U..., separated by commas): ").strip()
            
            p_ids = [p.strip() for p in participants_raw.split(",") if p.strip()]
            
            # Advanced fields
            hub_channel_id = input("Enter Hub Channel ID (Optional): ").strip() or None
            project_id = input("Enter Selected Project ID (Optional): ").strip() or None
            difficulty = input("Difficulty (easy, intermediate, hard) [intermediate]: ").strip() or "intermediate"
            deadline_h = int(input("Deadline Hours [48]: ").strip() or "48")
            
            created_at = input("Created At (YYYY-MM-DD HH:MM:SS) [Now]: ").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            console.print("\n[bold]Select Status:[/bold]")
            console.print("1. Recruiting 🟡")
            console.print("2. Active 🟢 (Default)")
            console.print("3. Evaluating 🟣")
            console.print("4. Completed 🔵")
            console.print("5. Failed 🔴")
            status_choice = input("Choice [2]: ") or "2"
            status_map = {"1": "recruiting", "2": "active", "3": "evaluating", "4": "completed", "5": "failed"}
            status = status_map.get(status_choice, "active")

            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 1. Hub table entry
            cursor.execute("""
                INSERT INTO challenge_hubs (
                    id, creator_id, theme, team_size, status, 
                    challenge_channel_id, hub_channel_id, selected_project_id,
                    difficulty, deadline_hours, created_at, started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hub_id, creator_id, theme, len(p_ids), status, 
                channel_id, hub_channel_id, project_id,
                difficulty, deadline_h, created_at, created_at
            ))
            
            # 2. Participants
            for m_id in p_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO challenge_participants (id, challenge_hub_id, user_id, role)
                    VALUES (?, ?, ?, ?)
                """, (str(uuid.uuid4()), hub_id, m_id, 'member' if m_id != creator_id else 'lead'))
                
            conn.commit()
            conn.close()
            
            console.print(f"[bold green]✅ Success! Manual record created with Hub ID: {hub_id[:12]}...[/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]❌ Error during manual creation: {e}[/bold red]")

    def export_challenge(self, challenge_id: str):
        """Export a challenge and its related data to JSON."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Handle Short ID
        target_id = challenge_id
        if len(challenge_id) < 30:
             cursor.execute("SELECT id FROM challenge_hubs WHERE id LIKE ?", (f"{challenge_id}%",))
             row = cursor.fetchone()
             if row: target_id = row['id']
             else:
                 console.print(f"[bold red]❌ Challenge not found: {challenge_id}[/bold red]")
                 conn.close()
                 return
        
        # 1. Hub
        cursor.execute("SELECT * FROM challenge_hubs WHERE id = ?", (target_id,))
        hub_row = cursor.fetchone()
        if not hub_row:
             console.print(f"[bold red]❌ Challenge not found: {challenge_id}[/bold red]")
             conn.close()
             return
        hub = dict(hub_row)
        
        # 2. Participants
        cursor.execute("SELECT * FROM challenge_participants WHERE challenge_hub_id = ?", (target_id,))
        participants = [dict(row) for row in cursor.fetchall()]
        
        # 3. Evaluations
        cursor.execute("SELECT * FROM challenge_evaluations WHERE challenge_hub_id = ?", (target_id,))
        evaluations = [dict(row) for row in cursor.fetchall()]
        
        # 4. Evaluators
        eval_ids = [e['id'] for e in evaluations]
        evaluators = []
        if eval_ids:
            placeholders = ", ".join(["?"] * len(eval_ids))
            cursor.execute(f"SELECT * FROM challenge_evaluators WHERE evaluation_id IN ({placeholders})", eval_ids)
            evaluators = [dict(row) for row in cursor.fetchall()]
            
        conn.close()
        
        data = {
            "type": "challenge_backup",
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "hub": hub,
            "participants": participants,
            "evaluations": evaluations,
            "evaluators": evaluators
        }
        
        import json
        json_output = json.dumps(data, indent=2, ensure_ascii=False)
        
        console.print(Panel(f"[bold green]✅ Challenge exported successfully![/bold green]\nCopy the JSON below to restore it later."))
        console.print(json_output)
        
        # Optionally save to file
        filename = f"challenge_{target_id[:8]}_backup.json"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(json_output)
        console.print(f"\n[dim]Also saved to: {filename}[/dim]")

    def restore_from_json(self):
        """Restore challenge data from a JSON backup."""
        import json
        
        console.print("[yellow]Paste your JSON content below. Press Ctrl+D (Linux/Mac) or Ctrl+Z (Win) followed by Enter when finished.[/yellow]")
        try:
            content = sys.stdin.read()
            if not content.strip():
                console.print("[red]No content provided.[/red]")
                return
                
            data = json.loads(content)
            if data.get("type") != "challenge_backup":
                 console.print("[red]❌ Error: Invalid backup format. Must be a 'challenge_backup' type.[/red]")
                 return
            
            hub = data["hub"]
            participants = data.get("participants", [])
            evaluations = data.get("evaluations", [])
            evaluators = data.get("evaluators", [])
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Start transaction
            cursor.execute("BEGIN TRANSACTION")
            
            try:
                # 1. Hub
                cols = ", ".join(hub.keys())
                placeholders = ", ".join(["?"] * len(hub))
                cursor.execute(f"INSERT OR REPLACE INTO challenge_hubs ({cols}) VALUES ({placeholders})", list(hub.values()))
                
                # 2. Participants
                for p in participants:
                    cols = ", ".join(p.keys())
                    placeholders = ", ".join(["?"] * len(p))
                    cursor.execute(f"INSERT OR REPLACE INTO challenge_participants ({cols}) VALUES ({placeholders})", list(p.values()))
                
                # 3. Evaluations
                for e in evaluations:
                    cols = ", ".join(e.keys())
                    placeholders = ", ".join(["?"] * len(e))
                    cursor.execute(f"INSERT OR REPLACE INTO challenge_evaluations ({cols}) VALUES ({placeholders})", list(e.values()))
                
                # 4. Evaluators
                for ev in evaluators:
                    cols = ", ".join(ev.keys())
                    placeholders = ", ".join(["?"] * len(ev))
                    cursor.execute(f"INSERT OR REPLACE INTO challenge_evaluators ({cols}) VALUES ({placeholders})", list(ev.values()))
                
                conn.commit()
                console.print(f"[bold green]✅ Success! Challenge restored: {hub['id'][:12]} ({hub.get('theme')})[/bold green]")
            except Exception as e:
                conn.rollback()
                console.print(f"[bold red]❌ Restoration failed: {e}[/bold red]")
            finally:
                conn.close()
                
        except json.JSONDecodeError as e:
            console.print(f"[bold red]❌ Error: Invalid JSON syntax. {e}[/bold red]")
        except Exception as e:
            console.print(f"[bold red]❌ Error: {e}[/bold red]")


def interactive_menu():
    """Show interactive menu."""
    manager = ChallengeManager()
    
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]🤖 Cemil Bot Challenge Manager v2.0[/bold cyan]", border_style="cyan"))
        
        console.print("[1] 🏆 List Challenges")
        console.print("[2] 🕵️  Check Stuck Users")
        console.print("[3] 📥 Import Slack Channel")
        console.print("[4] 🔍 Get Challenge Info")
        console.print("[5] 🔄 Update Status")
        console.print("[6] 👤 Reset User")
        console.print("[7] 🗑️  Delete Challenge")
        console.print("[8] 🛠️  Manual Entry (Create Form)")
        console.print("[9] 📥 Restore from JSON")
        console.print("[10] 📤 Export to JSON")
        console.print("[0] 🚪 Exit")
        
        choice = input("\n👉 Select an option: ")
        
        if choice == "1":
            console.print("\n[bold]Filter Status:[/bold]")
            console.print("1. All")
            console.print("2. Active 🟢")
            console.print("3. Recruiting 🟡")
            console.print("4. Completed 🔵")
            
            sub = input("Select filter [1]: ")
            status_map = {"1": None, "2": "active", "3": "recruiting", "4": "completed"}
            manager.list_challenges(status_map.get(sub, None))
            input("\nPress Enter to continue...")

        elif choice == "2":
            manager.check_stuck_users()
            input("\nPress Enter to continue...")
            
        elif choice == "3":
            cid = input("Enter Slack Channel ID (C...): ")
            if cid:
                manager.import_channel(cid)
                input("\nPress Enter to continue...")
                
        elif choice == "4":
            cid = input("Enter Challenge ID: ")
            if cid:
                manager.get_challenge_info(cid)
                input("\nPress Enter to continue...")
                
        elif choice == "5":
            cid = input("Enter Challenge ID: ")
            if cid:
                console.print("\n[bold]Select New Status:[/bold]")
                console.print("1. Recruiting 🟡")
                console.print("2. Active 🟢")
                console.print("3. Evaluating 🟣")
                console.print("4. Completed 🔵")
                console.print("5. Failed 🔴")
                console.print("6. Cancelled ⚫")
                
                s_choice = input("Select status: ")
                status_map = {
                    "1": "recruiting", "2": "active", "3": "evaluating", 
                    "4": "completed", "5": "failed", "6": "cancelled"
                }
                
                if s_choice in status_map:
                    manager.update_status(cid, status_map[s_choice])
                else:
                    console.print("[red]Invalid selection![/red]")
                input("\nPress Enter to continue...")
                
        elif choice == "6": # Corrected from duplicate '5'
            uid = input("Enter User Slack ID: ")
            if uid:
                manager.reset_user(uid)
                input("\nPress Enter to continue...")
                
        elif choice == "7": # Corrected from '6'
            cid = input("Enter Challenge ID: ")
            if cid:
                manager.delete_challenge(cid)
                input("\nPress Enter to continue...")
                
        elif choice == "8":
            manager.manual_create_challenge()
            input("\nPress Enter to continue...")
        
        elif choice == "9":
            manager.restore_from_json()
            input("\nPress Enter to continue...")

        elif choice == "10":
            cid = input("Enter Challenge ID to export: ")
            if cid:
                manager.export_challenge(cid)
            input("\nPress Enter to continue...")
                
        elif choice == "0":
            console.print("[yellow]Bye! 👋[/yellow]")
            break
        else:
            console.print("[red]Invalid choice![/red]")
            input("\nPress Enter to continue...")


def main():
    parser = argparse.ArgumentParser(description="Challenge Management Tool")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # List
    list_parser = subparsers.add_parser("list", help="List challenges")
    list_parser.add_argument("--status", help="Filter by status (active, completed, all)")
    list_parser.add_argument("--limit", type=int, default=20, help="Number of items to show")

    # Info
    info_parser = subparsers.add_parser("info", help="Show challenge details")
    info_parser.add_argument("id", help="Challenge ID (first few chars are enough)")

    # Status
    status_parser = subparsers.add_parser("status", help="Update challenge status")
    status_parser.add_argument("id", help="Challenge ID")
    status_parser.add_argument("new_status", help="New status (recruiting, active, completed, evaluating, failed)")

    # Delete
    del_parser = subparsers.add_parser("delete", help="Delete a challenge")
    del_parser.add_argument("id", help="Challenge ID")
    del_parser.add_argument("--yes", action="store_true", help="Skip confirmation")

    # Reset User
    reset_parser = subparsers.add_parser("reset-user", help="Fix a stuck user")
    reset_parser.add_argument("user_id", help="Slack User ID (e.g. U12345)")

    # Export
    export_parser = subparsers.add_parser("export", help="Export a challenge to JSON")
    export_parser.add_argument("id", help="Challenge ID")

    # Restore
    restore_parser = subparsers.add_parser("restore", help="Restore challenge from JSON")

    # Eğer argüman verilmemişse interaktif moda geç
    if len(sys.argv) == 1:
        try:
            interactive_menu()
        except KeyboardInterrupt:
            console.print("\n[yellow]Exiting...[/yellow]")
        return

    args = parser.parse_args()
    
    manager = ChallengeManager()

    if args.command == "list":
        manager.list_challenges(args.status, args.limit)
    elif args.command == "info":
        manager.get_challenge_info(args.id)
    elif args.command == "status":
        manager.update_status(args.id, args.new_status)
    elif args.command == "delete":
        manager.delete_challenge(args.id, args.yes)
    elif args.command == "reset-user":
        manager.reset_user(args.user_id)
    elif args.command == "export":
        manager.export_challenge(args.id)
    elif args.command == "restore":
        manager.restore_from_json()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
