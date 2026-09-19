"""Inspect/export/clear local recovery experience; no game interaction."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path(__file__).resolve().parents[1] / 'config/error_learning.sqlite3')
    parser.add_argument('operation', choices=('show', 'export', 'clear'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--all', action='store_true', help='Explicitly clear all local learning records')
    args = parser.parse_args()
    if args.operation == 'clear' and not args.all:
        parser.error('clear requires --all')
    if args.operation == 'export' and args.output is None:
        parser.error('export requires --output FILE')
    if args.output and args.output.resolve() == args.db.resolve():
        parser.error('output must not overwrite the database')
    if not args.db.exists():
        print('No learning records yet.')
        return
    try:
        mode = 'rw' if args.operation == 'clear' else 'ro'
        with closing(sqlite3.connect(args.db.resolve().as_uri() + '?mode=' + mode, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            if args.operation == 'clear':
                with connection:
                    connection.execute('DELETE FROM experience')
                    connection.execute('DELETE FROM errors')
                print('Cleared all local learning records. Restart running scripts to discard in-memory experience.')
                return
            data = dict(experiences=[dict(row) for row in connection.execute('SELECT * FROM experience ORDER BY id DESC LIMIT 5000')],
                        errors=[dict(row) for row in connection.execute('SELECT * FROM errors ORDER BY last_seen DESC LIMIT 1000')])
            text = json.dumps(data, ensure_ascii=False, indent=2)
            if args.operation == 'export':
                args.output.write_text(text, encoding='utf-8')
                print('Exported:', args.output)
            else:
                print(text)
    except (OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Unable to access learning records: {exc}\n')


if __name__ == '__main__':
    main()
