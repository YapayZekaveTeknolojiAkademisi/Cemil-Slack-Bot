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

from src.core.settings import get_settings

console = Console()

class ChallengeManager:
    def __init__(self):
        self.settings = get_settings()
        self.db_path = self.settings.database_path
        
        if not os.path.exists(self.db_path):
            console.print(f"[bold red]❌ Database not found at:[/bold red] {self.db_path}")
            sys.exit(1)

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
            # Delete children first
            cursor.execute("DELETE FROM challenge_participants WHERE challenge_hub_id = ?", (target_id,))
            cursor.execute("DELETE FROM challenge_evaluators WHERE evaluation_id IN (SELECT id FROM challenge_evaluations WHERE challenge_hub_id = ?)", (target_id,))
            cursor.execute("DELETE FROM challenge_evaluations WHERE challenge_hub_id = ?", (target_id,))
            
            # Delete parent
            cursor.execute("DELETE FROM challenge_hubs WHERE id = ?", (target_id,))
            conn.commit()
            
            console.print(f"[bold green]✅ Challenge deleted successfully![/bold green]")

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


def interactive_menu():
    """Show interactive menu."""
    manager = ChallengeManager()
    
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]🤖 Cemil Bot Challenge Manager[/bold cyan]", border_style="cyan"))
        
        console.print("[1] 🏆 List Challenges")
        console.print("[2] 🔍 Get Challenge Info")
        console.print("[3] 🔄 Update Status")
        console.print("[4] 👤 Reset User")
        console.print("[5] 🗑️  Delete Challenge")
        console.print("[0] 🚪 Exit")
        
        choice = input("\n👉 Select an option: ")
        
        if choice == "1":
            status = input("Filter status (active, completed, all) [default: all]: ") or None
            manager.list_challenges(status)
            input("\nPress Enter to continue...")
            
        elif choice == "2":
            cid = input("Enter Challenge ID: ")
            if cid:
                manager.get_challenge_info(cid)
                input("\nPress Enter to continue...")
                
        elif choice == "3":
            cid = input("Enter Challenge ID: ")
            status = input("Enter New Status (recruiting/active/completed/evaluating): ")
            if cid and status:
                manager.update_status(cid, status)
                input("\nPress Enter to continue...")
                
        elif choice == "4":
            uid = input("Enter User Slack ID: ")
            if uid:
                manager.reset_user(uid)
                input("\nPress Enter to continue...")
                
        elif choice == "5":
            cid = input("Enter Challenge ID: ")
            if cid:
                manager.delete_challenge(cid)
                input("\nPress Enter to continue...")
                
        elif choice == "0":
            console.print("[yellow]Bye! 👋[/yellow]")
            break
        else:
            console.print("[red]Invalid choice![/red]")


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
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
