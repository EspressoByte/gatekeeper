#!/usr/bin/python3
import re
import csv
import io
import sys
import glob
import getpass
import pwd
import subprocess
import os
import readline
from datetime import datetime

DATA_DIR = '/opt/secret/data'


def get_user_info():
    try:
        username = sys.argv[1] if len(sys.argv) > 1 else getpass.getuser()
        pw = pwd.getpwnam(username)
        return {
            'username': pw.pw_name,
            'home':     pw.pw_dir,
            'uid':      pw.pw_uid,
            'gid':      pw.pw_gid,
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def file_age(file_path):
    try:
        name = os.path.basename(file_path).replace('data_', '').replace('.csv', '')
        file_time = datetime.strptime(name, '%Y%m%d_%H%M%S')
        seconds = int((datetime.now() - file_time).total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            remaining_mins = minutes % 60
            if remaining_mins:
                return f"{hours}h {remaining_mins}m ago"
            return f"{hours}h ago"
        days = hours // 24
        if days < 7:
            remaining_hours = hours % 24
            remaining_mins = minutes % 60
            parts = [f"{days}d"]
            if remaining_hours:
                parts.append(f"{remaining_hours}h")
            if remaining_mins:
                parts.append(f"{remaining_mins}m")
            return " ".join(parts) + " ago"
        if days < 30:
            weeks = days // 7
            remaining_days = days % 7
            if remaining_days:
                return f"{weeks}w {remaining_days}d ago"
            return f"{weeks}w ago"
        months = days // 30
        remaining_weeks = (days % 30) // 7
        if remaining_weeks:
            return f"{months}mo {remaining_weeks}w ago"
        return f"{months}mo ago"
    except ValueError:
        return "unknown age"


def purge_old_csvs():
    files = glob.glob(os.path.join(DATA_DIR, 'data_*.csv'))
    if not files:
        return
    now = datetime.now()
    newer = [f for f in files if (now - datetime.strptime(
        os.path.basename(f).replace('data_', '').replace('.csv', ''), '%Y%m%d_%H%M%S'
    )).days < 30]
    if not newer:
        return  # no recent file to fall back on, keep everything
    for f in files:
        age_days = (now - datetime.strptime(
            os.path.basename(f).replace('data_', '').replace('.csv', ''), '%Y%m%d_%H%M%S'
        )).days
        if age_days >= 30:
            os.remove(f)
            print(f"Deleted old data file: {os.path.basename(f)}")


def find_latest_csv():
    files = glob.glob(os.path.join(DATA_DIR, 'data_*.csv'))
    return sorted(files)[-1] if files else None


def load_lines(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
    return lines[1:] if lines else []  # skip header row


def search_lines(lines, patterns):
    matching_lines = []
    for line in lines:
        try:
            if all(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
                matching_lines.append(line.strip())
        except re.error:
            print("Invalid search pattern entered.")
            return []
    return matching_lines


def run_sync(state):
    try:
        subprocess.run(["python3", "/opt/secret/ise_fetch.py"], check=True, timeout=300)
        print("\nFormatting data...")
        subprocess.run(["python3", "/opt/secret/export_devices_csv.py"], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        new_file = find_latest_csv()
        if new_file and new_file != state['file_path']:
            state['lines'] = load_lines(new_file)
            state['file_path'] = new_file
        purge_old_csvs()
        setup_completer(state)
        print(f"Sync complete — {len(state['lines'])} devices loaded.")
    except subprocess.TimeoutExpired as e:
        print(f"\nSync failed: timed out after {e.timeout}s.")
    except subprocess.CalledProcessError as e:
        print(f"\nSync failed (exit code {e.returncode}).")
    except FileNotFoundError as e:
        print(f"\nSync failed: {e}")
    except KeyboardInterrupt:
        print("\nSync cancelled.")


def handle_command(user_input, state):
    parts = user_input[1:].split()
    if not parts:
        print("No command entered. Type /help for available commands.")
        return

    cmd = parts[0].lower()

    if cmd == 'help':
        log_status = "on" if state['log_enabled'] else "off"
        ping_status = "on" if state['ping_mode'] else "off"
        print("Current setup:")
        print(f"  Username : {state['username']}")
        print(f"  Data file: {os.path.basename(state['file_path'])} ({file_age(state['file_path'])})")
        print(f"  Logging  : {log_status}")
        print(f"  Ping mode: {ping_status}")
        print()
        print("Available commands:")
        print("  /user <username>  - Set the SSH username")
        print("  /sync             - Fetch ISE device data")
        print(f"  /log              - Toggle SSH session logging (currently: {log_status})")
        print(f"  /ping             - Toggle ping mode instead of SSH (currently: {ping_status})")
        print("  /help             - Show this help message")

    elif cmd == 'user':
        if len(parts) < 2:
            print("Usage: /user <username>")
        else:
            state['username'] = parts[1]
            print(f"SSH username set to: {state['username']}")

    elif cmd == 'sync':
        run_sync(state)

    elif cmd == 'log':
        state['log_enabled'] = not state['log_enabled']
        if state['log_enabled']:
            print("Session logging enabled.")
        else:
            print("Session logging disabled.")

    elif cmd == 'ping':
        state['ping_mode'] = not state['ping_mode']
        if state['ping_mode']:
            print("Ping mode enabled. Matches will be pinged instead of SSH'd.")
        else:
            print("Ping mode disabled. Matches will SSH as normal.")

    else:
        print(f"Unknown command: /{cmd}. Type /help for available commands.")


def build_vocab(lines):
    vocab = set()
    for line in lines:
        try:
            row = next(csv.reader(io.StringIO(line.strip())))
        except StopIteration:
            continue
        if len(row) < 4:
            continue
        name = row[0].strip()
        vocab.add(name)
        vocab.update(t for t in re.split(r'[-._]', name) if t)
        for seg in row[2].split(' > '):
            seg = seg.strip()
            if seg:
                vocab.add(seg)
        dtype = row[3].strip()
        if dtype:
            vocab.add(dtype)
    return sorted(vocab)


def make_completer(vocab):
    def completer(text, state):
        matches = [w for w in vocab if w.lower().startswith(text.lower())]
        return matches[state] if state < len(matches) else None
    return completer


def setup_completer(state):
    vocab = build_vocab(state['lines'])
    readline.set_completer(make_completer(vocab))
    readline.set_completer_delims(', ')
    if 'libedit' in (readline.__doc__ or ''):
        readline.parse_and_bind('bind ^I rl_complete')
    else:
        readline.parse_and_bind('tab: complete')


def main():
    user_info = get_user_info()
    if not user_info:
        print("Unable to determine the current user.")
        sys.exit(1)

    LOG_DIR = os.path.join(user_info['home'], "session_logs")

    state = {
        'username': user_info['username'],
        'uid':      user_info['uid'],
        'gid':      user_info['gid'],
        'log_dir':  LOG_DIR,
        'file_path': None,
        'lines': [],
        'log_enabled': False,
        'ping_mode': False,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    state['file_path'] = find_latest_csv()
    if not state['file_path']:
        state['file_path'] = os.path.join(DATA_DIR, f"data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        open(state['file_path'], 'w').close()
        print(f"No data file found. Created empty: {os.path.basename(state['file_path'])}")

    try:
        state['lines'] = load_lines(state['file_path'])
    except FileNotFoundError:
        print(f"Error: '{os.path.basename(state['file_path'])}' not found.")
        sys.exit(1)

    setup_completer(state)

    print(f"Current username: {state['username']}")
    print(f"Using data file:  {os.path.basename(state['file_path'])}")

    while True:
        try:
            prompt = "[Ping]: " if state['ping_mode'] else "Enter search: "
            user_input = input(prompt)
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if user_input.lower() in ('exit', 'quit'):
            break

        if not user_input.strip():
            continue

        if user_input.startswith('/'):
            handle_command(user_input, state)
            continue

        patterns = [p.strip() for p in user_input.split(',')]
        matching_lines = search_lines(state['lines'], patterns)

        if matching_lines:
            print("Matching Devices:")
            for match in matching_lines:
                match = next(csv.reader(io.StringIO(match)))
                if len(match) < 4:
                    print(f"Skipping malformed line: {match}")
                    continue
                print(match[0], "-", match[1])
            if len(matching_lines) == 1:
                match = next(csv.reader(io.StringIO(matching_lines[0])))
                if len(match) < 4:
                    print("Cannot connect: malformed device entry.")
                else:
                    if state['ping_mode']:
                        print("PINGING...", match[1])
                        try:
                            subprocess.run(["ping", match[1]])
                        except KeyboardInterrupt:
                            print("\nPing cancelled.")
                    else:
                        print("CONNECTING...", match[1])
                        try:
                            if state['log_enabled']:
                                os.makedirs(state['log_dir'], exist_ok=True)
                                os.chown(state['log_dir'], state['uid'], state['gid'])
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                log_file = os.path.join(state['log_dir'], f"{match[0]}_{timestamp}.log")
                                print(f"Logging session to {log_file}")
                                subprocess.run(["script", "-q", log_file, "ssh", f"{state['username']}@{match[1]}"])
                                os.chown(log_file, state['uid'], state['gid'])
                            else:
                                subprocess.run(["ssh", f"{state['username']}@{match[1]}"])
                        except KeyboardInterrupt:
                            print("\nConnection cancelled.")
        else:
            print("No matching lines found.")


if __name__ == "__main__":
    main()
