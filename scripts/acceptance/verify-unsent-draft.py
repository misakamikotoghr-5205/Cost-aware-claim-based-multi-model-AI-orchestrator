"""Read only message counts in the isolated native profile after GUI acceptance."""
import argparse
import json
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument('--profile-home', type=Path, required=True)
parser.add_argument('--report', type=Path, required=True)
args = parser.parse_args()
database = args.profile_home.resolve() / 'state.db'
counts = {'database_present': database.is_file(), 'sessions': 0, 'messages': 0}
if database.is_file():
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        for table in ('sessions', 'messages'):
            counts[table] = connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
    finally:
        connection.close()
counts['no_automatic_submission'] = counts['messages'] == 0
args.report.write_text(json.dumps(counts, indent=2), encoding='utf8')
if not counts['no_automatic_submission']:
    raise SystemExit('The isolated draft-only acceptance profile contains sent messages')
print(json.dumps(counts))
