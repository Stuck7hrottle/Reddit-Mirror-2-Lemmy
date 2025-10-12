#!/usr/bin/env python3
import sqlite3
import os
import argparse
from datetime import datetime

# ───────────────────────────────
# Setup
# ───────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "jobs.db")

if not os.path.exists(DB_PATH):
    print(f"❌ No jobs database found at {DB_PATH}")
    exit(1)

# ───────────────────────────────
# Color helpers (ANSI escape codes)
# ───────────────────────────────
def color(text, fg=None):
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
    }
    if fg and fg in colors:
        return f"{colors[fg]}{text}{colors['reset']}"
    return text

# ───────────────────────────────
# Parse CLI arguments
# ───────────────────────────────
parser = argparse.ArgumentParser(description="Manage and inspect Lemmy Mirror background jobs.")
parser.add_argument("--status", help="Filter jobs by status (e.g., queued, done, failed, in_progress)")
parser.add_argument("--delete", help="Delete all jobs with a specific status (e.g., failed, done)")
parser.add_argument("--clear", action="store_true", help="Delete ALL jobs after confirmation")
parser.add_argument("--requeue", action="store_true", help="Move all failed jobs back to queued")
args = parser.parse_args()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ───────────────────────────────
# Deletion logic
# ───────────────────────────────
if args.delete:
    status = args.delete.lower()
    confirm = input(f"⚠️  Delete all jobs with status '{status}'? (y/N): ").strip().lower()
    if confirm == "y":
        deleted = cur.execute("DELETE FROM jobs WHERE status=?", (status,)).rowcount
        conn.commit()
        print(color(f"🗑️  Deleted {deleted} '{status}' jobs.\n", "red"))
    else:
        print("❌ Cancelled.")
    conn.close()
    exit(0)

if args.clear:
    confirm = input("⚠️  Delete ALL jobs from the database? (y/N): ").strip().lower()
    if confirm == "y":
        cur.execute("DELETE FROM jobs")
        conn.commit()
        print(color("🧹 All jobs cleared.\n", "red"))
    else:
        print("❌ Cancelled.")
    conn.close()
    exit(0)

# ───────────────────────────────
# Requeue logic
# ───────────────────────────────
if args.requeue:
    confirm = input("♻️  Requeue all FAILED jobs back to 'queued'? (y/N): ").strip().lower()
    if confirm == "y":
        updated = cur.execute("UPDATE jobs SET status='queued', retries=0 WHERE status='failed'").rowcount
        conn.commit()
        print(color(f"♻️  Requeued {updated} failed jobs.\n", "yellow"))
    else:
        print("❌ Cancelled.")
    conn.close()
    exit(0)

# ───────────────────────────────
# Query logic
# ───────────────────────────────
if args.status:
    rows = cur.execute(
        "SELECT id, type, status, retries, next_run FROM jobs WHERE status=? ORDER BY id ASC",
        (args.status.lower(),),
    ).fetchall()
else:
    rows = cur.execute(
        "SELECT id, type, status, retries, next_run FROM jobs ORDER BY id ASC"
    ).fetchall()

if not rows:
    print("ℹ️  No matching jobs found.")
    conn.close()
    exit(0)

# ───────────────────────────────
# Display output
# ───────────────────────────────
print(color("\n📋 JOB QUEUE OVERVIEW\n", "cyan"))
print(f"{'ID':<4} {'TYPE':<15} {'STATUS':<12} {'RETRIES':<8} {'NEXT RUN':<20}")
print("-" * 65)

for (job_id, job_type, status, retries, next_run) in rows:
    if status == "done":
        status_colored = color(status, "green")
    elif status == "queued":
        status_colored = color(status, "yellow")
    elif status == "failed":
        status_colored = color(status, "red")
    elif status == "in_progress":
        status_colored = color(status, "blue")
    else:
        status_colored = status

    time_str = (
        datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M:%S")
        if next_run
        else "—"
    )

    print(f"{job_id:<4} {job_type:<15} {status_colored:<12} {retries:<8} {time_str:<20}")

print("\n✅ Done.\n")
conn.close()
