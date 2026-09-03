"""
PM Agents - Automated Jira/Confluence Reports
Runs via GitHub Actions on schedule or manually.
Updates Confluence pages with fresh Jira data.
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict

# Configuration
JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://hybrent.atlassian.net")
JIRA_USER = os.environ.get("JIRA_USERNAME", "")
JIRA_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
CONFLUENCE_BASE = os.environ.get("CONFLUENCE_BASE_URL", "https://hybrent.atlassian.net/wiki")
CONFLUENCE_PAGE_ID = os.environ.get("CONFLUENCE_PAGE_ID", "3145236484")

AUTH = (JIRA_USER, JIRA_TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
PROJECTS = ["HYB2", "IMA", "PRO", "SMO"]


def jira_search(jql, fields="summary,status,assignee,priority,created,updated,resolutiondate", max_results=100):
    """Search Jira issues using JQL. Paginates via nextPageToken until all results are collected."""
    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    all_issues = []
    next_page_token = None
    while True:
        payload = {
            "jql": jql,
            "fields": fields.split(",") if isinstance(fields, str) else fields,
            "maxResults": max_results,
        }
        if next_page_token:
            payload["nextPageToken"] = next_page_token
        resp = requests.post(url, json=payload, auth=AUTH, headers=HEADERS)
        if resp.status_code != 200:
            print(f"Jira search failed ({resp.status_code}): {resp.text[:200]}")
            break
        data = resp.json()
        all_issues.extend(data.get("issues", []))
        next_page_token = data.get("nextPageToken")
        if not next_page_token or not data.get("issues"):
            break
    return all_issues


def get_confluence_page(page_id):
    """Fetch a Confluence page's current body (storage format) and version."""
    url = f"{CONFLUENCE_BASE}/rest/api/content/{page_id}"
    params = {"expand": "body.storage,version"}
    resp = requests.get(url, params=params, auth=AUTH, headers=HEADERS)
    if resp.status_code != 200:
        print(f"Failed to get page: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()


def update_confluence_page(page_id, title, storage_html):
    """Update a Confluence page with new storage-format HTML content."""
    current = get_confluence_page(page_id)
    if not current:
        return False

    version = current.get("version", {}).get("number", 1)

    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "body": {"storage": {"value": storage_html, "representation": "storage"}},
        "version": {"number": version + 1}
    }
    url = f"{CONFLUENCE_BASE}/rest/api/content/{page_id}"
    resp = requests.put(url, json=payload, auth=AUTH, headers=HEADERS)
    if resp.status_code == 200:
        print(f"Confluence page updated: {title}")
        return True
    print(f"Failed to update page ({resp.status_code}): {resp.text[:200]}")
    return False


def html_table(headers, rows):
    """Build a simple Confluence storage-format HTML table."""
    thead = "".join(f"<th>{h}</th>" for h in headers)
    trs = ""
    for row in rows:
        tds = "".join(f"<td>{c}</td>" for c in row)
        trs += f"<tr>{tds}</tr>"
    return f"<table><tbody><tr>{thead}</tr>{trs}</tbody></table>"


def get_week_number(date_str):
    """Get ISO week number from date string."""
    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00").split(".")[0])
    return dt.isocalendar()[1]


# ============================================================
# AGENT: Standup Facilitator
# ============================================================
def run_standup():
    """Generate standup report grouped by assignee."""
    print("\n=== STANDUP FACILITATOR AGENT ===\n")

    updated_today = []
    in_progress = []
    blockers = []

    for project in PROJECTS:
        updated_today += jira_search(f'project = {project} AND updated >= -1d')
        in_progress += jira_search(
            f'project = {project} AND status in ("In-Progress", "In Progress", "UI Development", "Dev-Review", "Ready-For-QA")'
        )
        blockers += jira_search(
            f'project = {project} AND (status = Blocked OR priority = Blocker) AND status not in ("Done/QA Complete", Done, Removed)'
        )

    # Group by assignee
    by_person = defaultdict(lambda: {"updated": [], "in_progress": [], "blockers": []})

    for issue in updated_today:
        assignee = issue["fields"].get("assignee", {})
        name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        by_person[name]["updated"].append(f'{issue["key"]}: {issue["fields"]["summary"]}')

    for issue in in_progress:
        assignee = issue["fields"].get("assignee", {})
        name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        by_person[name]["in_progress"].append(f'{issue["key"]}: {issue["fields"]["summary"]}')

    for issue in blockers:
        assignee = issue["fields"].get("assignee", {})
        name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        by_person[name]["blockers"].append(f'{issue["key"]}: {issue["fields"]["summary"]}')

    # Print report
    print(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    for person, data in sorted(by_person.items()):
        if not any([data["updated"], data["in_progress"], data["blockers"]]):
            continue
        print(f"\n👤 {person}")
        if data["in_progress"]:
            print("  TODAY:")
            for item in data["in_progress"][:5]:
                print(f"    - {item}")
        if data["blockers"]:
            print("  ⚠️ BLOCKERS:")
            for item in data["blockers"]:
                print(f"    - {item}")
    print(f"\n{'='*60}")
    print(f"Total in progress: {len(in_progress)} | Blockers: {len(blockers)}")


# ============================================================
# AGENT: Sprint Health
# ============================================================
def run_sprint_health():
    """Analyze sprint completion and health."""
    print("\n=== SPRINT HEALTH AGENT ===\n")

    # Get sprint issues for HYB2 board
    url = f"{JIRA_BASE}/rest/agile/1.0/board/32/sprint"
    params = {"state": "active"}
    resp = requests.get(url, params=params, auth=AUTH, headers=HEADERS)

    if resp.status_code != 200:
        print("Could not fetch active sprints")
        return

    sprints = resp.json().get("values", [])
    for sprint in sprints:
        sprint_id = sprint["id"]
        sprint_name = sprint.get("name", "Unknown")
        print(f"Sprint: {sprint_name}")
        print(f"Goal: {sprint.get('goal', 'No goal set')}")

        # Get sprint issues
        url = f"{JIRA_BASE}/rest/agile/1.0/sprint/{sprint_id}/issue"
        params = {"fields": "status,assignee,summary", "maxResults": 100}
        resp = requests.get(url, params=params, auth=AUTH, headers=HEADERS)

        if resp.status_code != 200:
            continue

        issues = resp.json().get("issues", [])
        total = len(issues)
        done = sum(1 for i in issues if i["fields"]["status"]["statusCategory"]["key"] == "done")
        in_prog = sum(1 for i in issues if i["fields"]["status"]["statusCategory"]["key"] == "indeterminate")
        todo = total - done - in_prog

        pct = (done / total * 100) if total > 0 else 0
        health = "🟢 GREEN" if pct > 70 else "🟡 YELLOW" if pct > 50 else "🔴 RED"

        print(f"\nHealth: {health}")
        print(f"Completion: {done}/{total} ({pct:.0f}%)")
        print(f"  Done: {done} | In Progress: {in_prog} | To-Do: {todo}")


# ============================================================
# AGENT: Status Report Generator
# ============================================================
def run_status_report():
    """Generate weekly status report."""
    print("\n=== STATUS REPORT GENERATOR ===\n")
    print(f"Report Date: {datetime.now().strftime('%B %d, %Y')}")
    print(f"{'='*60}\n")

    for project in PROJECTS:
        resolved = jira_search(f'project = {project} AND status changed to ("Done/QA Complete", Done) AFTER -7d')
        blockers = jira_search(
            f'project = {project} AND (status = Blocked OR priority = Blocker) AND status not in ("Done/QA Complete", Done, Removed)'
        )
        in_progress = jira_search(
            f'project = {project} AND status in ("In-Progress", "In Progress")'
        )

        print(f"📁 {project}")
        print(f"  Completed this week: {len(resolved)}")
        print(f"  In progress: {len(in_progress)}")
        print(f"  Blockers: {len(blockers)}")
        if blockers:
            for b in blockers[:3]:
                print(f"    ⚠️ {b['key']}: {b['fields']['summary'][:50]}")
        print()


# ============================================================
# AGENT: Risk Management
# ============================================================
def run_risk_management():
    """Deep risk analysis."""
    print("\n=== RISK MANAGEMENT AGENT ===\n")

    # Blockers
    blockers = jira_search(
        f'priority in (Blocker, Critical) AND status not in ("Done/QA Complete", Done, Removed) AND project in ({",".join(PROJECTS)})'
    )
    print(f"🔴 CRITICAL/BLOCKER ITEMS: {len(blockers)}")
    for b in blockers:
        print(f"  [{b['fields']['priority']['name']}] {b['key']}: {b['fields']['summary'][:60]}")
        assignee = b['fields'].get('assignee', {})
        print(f"    Assignee: {assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'}")

    # Stale items
    stale = jira_search(
        f'status not in ("Done/QA Complete", Done, "To-Do", "To Do", Removed) AND updated <= -14d AND project in ({",".join(PROJECTS)})'
    )
    print(f"\n🟡 STALE ITEMS (no update 14+ days): {len(stale)}")
    for s in stale[:10]:
        print(f"  {s['key']}: {s['fields']['summary'][:60]}")

    # Blocked items
    blocked = jira_search(f'status = Blocked AND project in ({",".join(PROJECTS)})')
    print(f"\n🟠 BLOCKED ITEMS: {len(blocked)}")
    for b in blocked:
        print(f"  {b['key']}: {b['fields']['summary'][:60]}")


# ============================================================
# AGENT: Weekly SMO Update
# ============================================================
def run_weekly_smo_update():
    """Update SMO Confluence tracker."""
    print("\n=== WEEKLY SMO TRACKER UPDATE ===\n")

    created = jira_search('project = SMO AND created >= -8w', max_results=200)
    resolved = jira_search(
        'project = SMO AND status in ("Done/QA Complete", Done) AND updated >= -8w',
        max_results=200
    )

    # Group by week
    weeks_created = defaultdict(int)
    weeks_resolved = defaultdict(int)

    for issue in created:
        week = get_week_number(issue["fields"]["created"])
        weeks_created[week] += 1

    for issue in resolved:
        updated = issue["fields"].get("updated", "")
        if updated:
            week = get_week_number(updated)
            weeks_resolved[week] += 1

    # Print summary
    all_weeks = sorted(set(list(weeks_created.keys()) + list(weeks_resolved.keys())))
    total_open = 0

    print(f"{'Week':<8} {'Created':<10} {'Resolved':<10} {'Total Open':<12}")
    print("-" * 42)
    for week in all_weeks:
        c = weeks_created.get(week, 0)
        r = weeks_resolved.get(week, 0)
        total_open += (c - r)
        print(f"W{week:<7} {c:<10} {r:<10} {total_open:<12}")

    print(f"\nTotal Created: {sum(weeks_created.values())}")
    print(f"Total Resolved: {sum(weeks_resolved.values())}")
    print(f"Net Open: {total_open}")

    # TODO: Update Confluence page (requires storage format conversion)
    print(f"\nConfluence page {CONFLUENCE_PAGE_ID} would be updated here.")


# ============================================================
# AGENT: Executive Dashboard
# ============================================================
def run_executive_dashboard():
    """Generate executive-level portfolio summary."""
    print("\n=== EXECUTIVE DASHBOARD ===\n")
    print(f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}")
    print(f"\n{'Project':<8} {'Health':<8} {'Done':<6} {'InProg':<8} {'ToDo':<6} {'Blockers':<10}")
    print("-" * 50)

    for project in PROJECTS:
        all_issues = jira_search(f'project = {project} AND status not in (Removed) AND created >= -30d')
        done = sum(1 for i in all_issues if i["fields"]["status"]["statusCategory"]["key"] == "done")
        in_prog = sum(1 for i in all_issues if i["fields"]["status"]["statusCategory"]["key"] == "indeterminate")
        todo = len(all_issues) - done - in_prog
        blockers = sum(1 for i in all_issues if i["fields"]["priority"]["name"] in ("Blocker", "Critical"))

        pct = (done / len(all_issues) * 100) if all_issues else 0
        health = "🟢" if pct > 60 else "🟡" if pct > 40 else "🔴"

        print(f"{project:<8} {health:<8} {done:<6} {in_prog:<8} {todo:<6} {blockers:<10}")


# ============================================================
# AGENT: Retrospective
# ============================================================
def run_retrospective():
    """Generate data-driven retrospective inputs."""
    print("\n=== RETROSPECTIVE AGENT ===\n")

    for project in PROJECTS:
        completed = jira_search(
            f'project = {project} AND status in ("Done/QA Complete", Done) AND updated >= -14d'
        )
        not_completed = jira_search(
            f'project = {project} AND status not in ("Done/QA Complete", Done, Removed) AND sprint in openSprints()'
        )

        if not completed and not not_completed:
            continue

        print(f"\n📁 {project}")
        print(f"  ✅ Completed: {len(completed)}")
        print(f"  ❌ Not completed: {len(not_completed)}")
        completion_rate = len(completed) / (len(completed) + len(not_completed)) * 100 if (completed or not_completed) else 0
        print(f"  📊 Completion rate: {completion_rate:.0f}%")


# ============================================================
# AGENT: Backlog Grooming
# ============================================================
def run_backlog_grooming():
    """Analyze backlog health."""
    print("\n=== BACKLOG GROOMING AGENT ===\n")

    stale = jira_search(
        f'status in ("To-Do", "To Do") AND updated <= -30d AND project in ({",".join(PROJECTS)})'
    )
    print(f"🧹 STALE BACKLOG (30+ days untouched): {len(stale)}")
    for s in stale[:10]:
        print(f"  {s['key']}: {s['fields']['summary'][:50]}")

    unassigned = jira_search(
        f'assignee = EMPTY AND status in ("To-Do", "To Do") AND project in ({",".join(PROJECTS)})'
    )
    print(f"\n👤 UNASSIGNED BACKLOG: {len(unassigned)}")
    for u in unassigned[:10]:
        print(f"  {u['key']}: {u['fields']['summary'][:50]}")


# ============================================================
# AGENT: Sprint Planning
# ============================================================
def run_sprint_planning():
    """Generate sprint planning inputs."""
    print("\n=== SPRINT PLANNING AGENT ===\n")

    # High priority backlog
    candidates = jira_search(
        f'status in ("To-Do", "To Do") AND priority in (Blocker, Critical, Major) AND project in ({",".join(PROJECTS)})'
    )
    print(f"📋 HIGH PRIORITY CANDIDATES: {len(candidates)}")
    for c in candidates[:15]:
        print(f"  [{c['fields']['priority']['name']}] {c['key']}: {c['fields']['summary'][:50]}")


# ============================================================
# AGENT: Dependency Tracking
# ============================================================
def run_dependency_tracking():
    """Track cross-project dependencies."""
    print("\n=== DEPENDENCY TRACKING AGENT ===\n")

    blocked = jira_search(f'status = Blocked AND project in ({",".join(PROJECTS)})')
    print(f"🔗 BLOCKED ITEMS (dependencies): {len(blocked)}")
    for b in blocked:
        assignee = b['fields'].get('assignee', {})
        name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
        print(f"  {b['key']}: {b['fields']['summary'][:50]} (Owner: {name})")


# ============================================================
# AGENT: Release Scope Deviation Tracker
# ============================================================
SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")

# Map: version -> Confluence page ID. Add new releases here.
RELEASE_DASHBOARDS = {
    "6.4.0": {"project": "HYB2", "page_id": "3288694785"},
    "6.3.0": {"project": "HYB2", "page_id": "3288006658"},
}


def _snapshot_path(project, version):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    safe_version = version.replace(".", "_")
    return os.path.join(SNAPSHOT_DIR, f"{project}_{safe_version}.json")


def _load_snapshot_file(project, version):
    """Load the full snapshot record: fixed baseline + most recent scope."""
    path = _snapshot_path(project, version)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def _save_snapshot_file(project, version, baseline_date, baseline_issues, current_date, current_issues):
    """
    Persist the release scope record.

    baseline_date/baseline_issues: the ORIGINAL committed scope. This is
    set once (first run, or explicit reset) and never changes on normal
    runs. All Added/Removed reporting is computed against this baseline,
    so the dashboard always answers "what changed vs. what we originally
    committed to" rather than a rolling day-over-day diff.

    current_date/current_issues: the most recent scope pulled from Jira,
    used for the "Current Scope" table and status breakdown.
    """
    path = _snapshot_path(project, version)
    with open(path, "w") as f:
        json.dump({
            "baseline_date": baseline_date,
            "baseline_issues": baseline_issues,
            "current_date": current_date,
            "current_issues": current_issues,
        }, f, indent=2)


def run_release_scope_tracker(version=None, reset_baseline=False):
    """
    Refresh release scope for one or all tracked versions.

    Compares CURRENT Jira scope against the ORIGINAL COMMITTED BASELINE
    (set once, the first time this runs for a version, or explicitly via
    reset_baseline=True). The dashboard always reports:
      - Original committed scope (count + full list)
      - Current scope (count + full list)
      - Added since baseline (items in current but not in original)
      - Removed since baseline (items in original but not in current)

    This is a fixed-reference comparison, not a rolling day-over-day diff,
    so re-running it multiple times in a day (or any day) always produces
    the same, correct answer — there is nothing to accidentally overwrite.

    reset_baseline=True replaces the stored baseline with the current
    scope (use this once, deliberately, when the committed scope changes
    or needs to be corrected — not as part of normal daily operation).
    """
    print("\n=== RELEASE SCOPE DEVIATION TRACKER ===\n")

    versions_to_check = [version] if version else list(RELEASE_DASHBOARDS.keys())

    for ver in versions_to_check:
        config = RELEASE_DASHBOARDS.get(ver)
        if not config:
            print(f"No dashboard configured for version {ver}, skipping.")
            continue

        project = config["project"]
        page_id = config["page_id"]

        print(f"\n--- {project} v{ver} ---")

        issues = jira_search(
            f'project = {project} AND fixVersion = "{ver}"',
            fields="summary,status,priority,assignee",
            max_results=200
        )

        current_map = {}
        for issue in issues:
            status_cat = issue["fields"]["status"]["statusCategory"]["key"]
            current_map[issue["key"]] = {
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
                "status_category": status_cat,
                "priority": issue["fields"]["priority"]["name"],
            }

        today = datetime.now().strftime("%Y-%m-%d")
        existing = _load_snapshot_file(project, ver)
        # Snapshots written by the old rolling-diff version of this tracker
        # don't have baseline_date/baseline_issues — treat them as "no
        # baseline yet" so this run establishes a fresh, correct baseline
        # rather than crashing on a KeyError.
        if existing and "baseline_date" not in existing:
            existing = None

        if reset_baseline or not existing:
            # First-ever run for this version, or an explicit baseline reset:
            # the current scope becomes the fixed baseline going forward.
            baseline_date = today
            baseline_issues = current_map
            reason = "Baseline reset requested" if reset_baseline else "No baseline found"
            print(f"{reason} — current scope ({len(current_map)} issues) established as the committed baseline.")
        else:
            baseline_date = existing["baseline_date"]
            baseline_issues = existing["baseline_issues"]
            print(f"Committed baseline: {baseline_date} ({len(baseline_issues)} issues)")

        baseline_keys = set(baseline_issues.keys())
        current_keys = set(current_map.keys())

        added_keys = sorted(current_keys - baseline_keys)
        removed_keys = sorted(baseline_keys - current_keys)
        added_detail = [{"key": k, "summary": current_map[k]["summary"]} for k in added_keys]
        removed_detail = [{"key": k, "summary": baseline_issues[k].get("summary", "")} for k in removed_keys]

        print(f"Original committed scope: {len(baseline_issues)} issues (as of {baseline_date})")
        print(f"Current scope: {len(current_map)} issues")
        print(f"Added since baseline: {len(added_detail)} | Removed since baseline: {len(removed_detail)}")

        for a in added_detail:
            print(f"  🟢 ADDED: {a['key']} - {a['summary'][:60]}")
        for r in removed_detail:
            print(f"  🔴 REMOVED: {r['key']} - {r['summary'][:60]}")

        # Build status breakdown for the current scope
        status_counts = defaultdict(int)
        for v in current_map.values():
            status_counts[v["status"]] += 1
        status_rows = [[status, str(count)] for status, count in sorted(status_counts.items())]
        status_table = html_table(["Status", "Count"], status_rows)

        summary_rows = [[baseline_date, str(len(baseline_issues)), today, str(len(current_map)), str(len(added_detail)), str(len(removed_detail))]]
        summary_table = html_table(
            ["Baseline Date", "Original Committed Scope", "Last Refreshed", "Current Scope", "Added", "Removed"],
            summary_rows
        )

        baseline_rows = [[k, v["summary"][:80], v.get("status", "-")] for k, v in sorted(baseline_issues.items())] or [["-", "No issues in baseline", "-"]]
        baseline_table = html_table(["Key", "Summary", "Status at Baseline"], baseline_rows)

        current_rows = [[k, v["summary"][:80], v["status"], v["priority"]] for k, v in sorted(current_map.items())] or [["-", "No issues in scope", "-", "-"]]
        current_table = html_table(["Key", "Summary", "Status", "Priority"], current_rows)

        added_rows = [[a["key"], a["summary"][:80]] for a in added_detail] or [["-", "No additions since baseline"]]
        added_table = html_table(["Key", "Summary"], added_rows)

        removed_rows = [[r["key"], r["summary"][:80]] for r in removed_detail] or [["-", "No removals since baseline"]]
        removed_table = html_table(["Key", "Summary"], removed_rows)

        html = f"""
        <h2>Release Scope vs. Committed Baseline</h2>
        <p><strong>Project:</strong> {project} | <strong>Version:</strong> {ver}</p>
        <h3>Summary</h3>
        {summary_table}
        <h3>Added Since Baseline ({len(added_detail)})</h3>
        {added_table}
        <h3>Removed Since Baseline ({len(removed_detail)})</h3>
        {removed_table}
        <h3>Current Status Breakdown</h3>
        {status_table}
        <h3>Original Committed Scope ({len(baseline_issues)} issues, as of {baseline_date})</h3>
        {baseline_table}
        <h3>Current Scope ({len(current_map)} issues)</h3>
        {current_table}
        <p><em>This section is auto-generated by the Release Scope Deviation Tracker. Baseline set: {baseline_date}. Last run: {today}</em></p>
        """

        title = f"{project} v{ver} - Scope Commitment & Deviation Dashboard"
        update_confluence_page(page_id, title, html)

        _save_snapshot_file(
            project, ver,
            baseline_date=baseline_date,
            baseline_issues=baseline_issues,
            current_date=today,
            current_issues=current_map,
        )
        print(f"Snapshot saved for {project} v{ver}. (baseline unchanged: {baseline_date})")


# ============================================================
# AGENT: RAID Log
# ============================================================
def run_raid_log():
    """Generate RAID log from Jira data."""
    print("\n=== RAID LOG AGENT ===\n")

    risks = jira_search(
        f'priority in (Blocker, Critical) AND status not in ("Done/QA Complete", Done, Removed) AND project in ({",".join(PROJECTS)})'
    )
    issues = jira_search(f'status = Blocked AND project in ({",".join(PROJECTS)})')

    print(f"{'Type':<5} {'Key':<12} {'Description':<50} {'Priority':<10}")
    print("-" * 80)
    for r in risks:
        print(f"{'RISK':<5} {r['key']:<12} {r['fields']['summary'][:50]:<50} {r['fields']['priority']['name']:<10}")
    for i in issues:
        print(f"{'ISSUE':<5} {i['key']:<12} {i['fields']['summary'][:50]:<50} {'Blocked':<10}")


# ============================================================
# MAIN DISPATCHER
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python pm_agents.py <agent_name>")
        print("Options: morning, weekly, standup, sprint-health, status-report,")
        print("         risk-management, executive-dashboard, retrospective,")
        print("         backlog-grooming, sprint-planning, dependency-tracking, raid-log")
        sys.exit(1)

    agent = sys.argv[1]
    agent_override = os.environ.get("AGENT_TYPE", agent)

    if agent_override in ("morning", "all-morning"):
        run_standup()
        run_sprint_health()
    elif agent_override == "weekly":
        run_status_report()
        run_executive_dashboard()
        run_retrospective()
        run_weekly_smo_update()
    elif agent_override == "standup":
        run_standup()
    elif agent_override == "sprint-health":
        run_sprint_health()
    elif agent_override == "status-report":
        run_status_report()
    elif agent_override == "risk-management":
        run_risk_management()
    elif agent_override == "executive-dashboard":
        run_executive_dashboard()
    elif agent_override == "retrospective":
        run_retrospective()
    elif agent_override == "backlog-grooming":
        run_backlog_grooming()
    elif agent_override == "sprint-planning":
        run_sprint_planning()
    elif agent_override == "dependency-tracking":
        run_dependency_tracking()
    elif agent_override == "raid-log":
        run_raid_log()
    elif agent_override == "weekly-smo-update":
        run_weekly_smo_update()
    elif agent_override == "release-scope-tracker":
        target_version = sys.argv[2] if len(sys.argv) > 2 else None
        reset = os.environ.get("RESET_BASELINE", "false").lower() == "true"
        run_release_scope_tracker(target_version, reset_baseline=reset)
    else:
        print(f"Unknown agent: {agent_override}")
        sys.exit(1)


if __name__ == "__main__":
    main()
