"""Local success receipts, stored beside exports; previews only read them."""
import json
from pathlib import Path
import sqlite3

NAME = '.videotool-history.sqlite3'


def read(folder):
    path = folder / NAME
    if not path.exists():
        return []
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT receipt FROM completed')]


def save(item, output_fingerprint):
    path = item.output.parent / NAME
    if path.is_symlink():
        raise OSError('Conversion history is a symbolic link.')
    receipt = dict(source=str(item.source), fingerprint=list(item.fingerprint),
                   output=item.output.name, output_fingerprint=list(output_fingerprint),
                   location=item.location, preset=item.preset,
                   target_mb=item.target_mb if item.preset == 'aistudio' else None,
                   format_key=item.format_key if item.preset == 'davinci' else None,
                   processing_receipt=(item.receipt_path.name if item.receipt_path else None),
                   elapsed_seconds=item.elapsed_seconds,
                   source_duration=item.duration,
                   conversion_speed=(item.duration / item.elapsed_seconds
                                     if item.duration > 0 and item.elapsed_seconds and
                                     item.elapsed_seconds > 0 else None),
                   source_size=item.source.stat().st_size,
                   output_size=item.output.stat().st_size)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS completed (output TEXT PRIMARY KEY, receipt TEXT NOT NULL)')
        db.execute('INSERT OR REPLACE INTO completed VALUES (?, ?)',
                   (item.output.name, json.dumps(receipt)))


def matching(receipts, source, source_fingerprint, location, folder, fingerprint, preset='davinci',
             target_mb=380, format_key=''):
    result = matching_record(receipts, source, source_fingerprint, location, folder, fingerprint,
                             preset, target_mb, format_key)
    return result[0] if result else None


def matching_record(receipts, source, source_fingerprint, location, folder, fingerprint,
                    preset='davinci', target_mb=380, format_key=''):
    for receipt in receipts:
        if (receipt.get('source') != str(source) or receipt.get('location') != location or
                receipt.get('fingerprint') != list(source_fingerprint) or
                receipt.get('preset', 'davinci') != preset or
                (preset == 'davinci' and receipt.get('format_key') != format_key) or
                (preset == 'aistudio' and receipt.get('target_mb') != target_mb)):
            continue
        name = receipt.get('output', '')
        if not name or Path(name).name != name:
            continue
        output = folder / name
        try:
            if (not output.is_symlink() and output.is_file() and
                    list(fingerprint(output)) == receipt.get('output_fingerprint')):
                return output, receipt
        except OSError:
            continue
    return None
