"""Private daily accounting checkpoints; no database, sessions or per-tick writes."""
import configparser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time

SAVE_INTERVAL = 86400
MAX_DAY_BYTES = 4 * 1024 * 1024


def source_identities(config):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(config, encoding='utf-8'):
        raise ValueError('Cannot read daily archive source configuration')
    # Probe executable and live telemetry port do not change historical inputs.
    return {section[7:]: hashlib.sha256(json.dumps(
        {key: value for key, value in parser[section].items()
         if key not in ('python', 'codex_otel_port')}, sort_keys=True).encode()).hexdigest()
        for section in parser.sections() if section.startswith('source:')}


def _date(value):
    return (isinstance(value, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)) and
            datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d') == value)


def _rows(rows):
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError('Invalid daily accounting rows')
    groups = {}
    for row in rows:
        if (not isinstance(row, dict) or
                any(not isinstance(row.get(k), str) or len(row[k]) > 512 for k in ('app', 'model', 'cost_usd')) or
                any(type(row.get(k)) is not int or not 0 <= row[k] <= 2**63 - 1 for k in ('tokens', 'requests'))):
            raise ValueError('Invalid daily accounting row')
        try:
            cost = Decimal(row['cost_usd'])
        except InvalidOperation as error:
            raise ValueError('Invalid daily accounting cost') from error
        if not cost.is_finite() or cost < 0:
            raise ValueError('Invalid daily accounting cost')
        key = (row['app'], row['model'])
        value = groups.setdefault(key, {'app': key[0], 'model': key[1], 'tokens': 0, 'requests': 0, 'cost_usd': Decimal(0)})
        value['tokens'] += row['tokens']
        value['requests'] += row['requests']
        value['cost_usd'] += cost
    return [dict(value, cost_usd=str(value['cost_usd'])) for _, value in sorted(groups.items())]


class DailyArchive:
    """Latest absolute daily counts, partitioned by usage day and source identity.

    A fresh source replaces its covered dates, including deduplicated empty dates.
    Missing dates survive as explicitly archived totals. Offline archives cannot
    recheck copied response identities until their source reconnects.
    """
    def __init__(self, folder, config, *, start_writer=True):
        self.folder = Path(folder)
        self.identities = source_identities(config)
        self.days, self.dirty = {}, set()
        self.lock = threading.RLock()
        self.bytes_written = self.save_batches = 0
        self.last_saved = None
        self.initial_requested = False
        self.error = None
        self.wake = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.worker = None
        if self.folder.is_symlink():
            raise ValueError('Daily archive folder must not be a symlink')
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.folder.chmod(0o700)
        self._restore()
        if start_writer:
            self.worker = threading.Thread(target=self._writer, name='daily-usage-writer')
            self.worker.start()

    def _restore(self):
        for path in sorted(self.folder.glob('*.json')):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name):
                continue
            if path.is_symlink() or path.stat().st_size > MAX_DAY_BYTES:
                raise ValueError('Invalid or oversized daily accounting log')
            record = json.loads(path.read_bytes())
            if (not isinstance(record, dict) or record.get('version') != 1 or
                    not _date(record.get('date')) or record['date'] != path.stem or
                    type(record.get('saved_at')) not in (int, float) or
                    not math.isfinite(record['saved_at']) or record['saved_at'] < 0 or
                    not isinstance(record.get('sources'), list)):
                raise ValueError('Invalid daily accounting log')
            entries = {}
            for source in record['sources']:
                if (not isinstance(source, dict) or not isinstance(source.get('name'), str) or
                        not 0 < len(source['name']) <= 128 or
                        not isinstance(source.get('identity'), str) or
                        not re.fullmatch('[0-9a-f]{64}', source['identity']) or
                        not isinstance(source.get('collected_at'), str) or
                        not isinstance(source.get('timezone'), (str, type(None)))):
                    raise ValueError('Invalid daily archive source')
                key = (source['name'], source['identity'])
                if key in entries:
                    raise ValueError('Repeated daily archive source')
                entries[key] = {k: source[k] for k in ('collected_at', 'timezone')} | {'rows': _rows(source.get('rows'))}
            self.days[path.stem] = entries
            self.last_saved = max(self.last_saved or 0, record['saved_at'])

    def _selected(self):
        for day, entries in sorted(self.days.items()):
            for (name, identity), entry in sorted(entries.items()):
                if self.identities.get(name) == identity:
                    yield day, name, entry

    def startup_snapshot(self):
        with self.lock:
            rows, sources = [], {}
            for day, name, entry in self._selected():
                rows.extend(dict(row, date=day, host=name) for row in entry['rows'])
                info = sources.setdefault(name, dict(collected_at=None, timezone=None, status='archived'))
                if (info['collected_at'] or '') < entry['collected_at']:
                    info.update(collected_at=entry['collected_at'], timezone=entry['timezone'])
            if not sources:
                return None
            return {'generated_at': datetime.fromtimestamp(self.last_saved, timezone.utc).isoformat(),
                    'rows': rows, 'hourly_rows': [dict(row, hour='Unknown hour') for row in rows],
                    'session_rows': [], 'response_rows': [], 'sources': sources,
                    'host_date': datetime.now().date().isoformat(),
                    'host_timezone': datetime.now().astimezone().tzname(), 'caveats': [],
                    'warnings': ['Daily archive loaded. Waiting for current machine readings; session and hourly detail are unavailable.']}

    def merge(self, data):
        """Merge public daily counts without adding a checkpoint to fresh totals."""
        groups = {}
        for row in data['rows']:
            if row['host'] not in self.identities or not _date(row['date']):
                raise ValueError('Unexpected daily accounting source or date')
            groups.setdefault((row['date'], row['host']), []).append(row)
        groups = {key: _rows(rows) for key, rows in groups.items()}
        covered = set(groups)
        with self.lock:
            for name, info in data['sources'].items():
                if (name not in self.identities or info.get('status') not in ('fresh', 'stale') or
                        not info.get('collected_at')):
                    continue
                dates = set(info.get('covered_dates', [])) | {day for day, host in groups if host == name}
                if any(not _date(day) for day in dates):
                    raise ValueError('Invalid daily accounting date coverage')
                key = (name, self.identities[name])
                for day in dates:
                    covered.add((day, name))
                    rows = groups.get((day, name), [])
                    old = self.days.setdefault(day, {}).get(key)
                    if not old or old['rows'] != rows or day == max(dates):
                        entry = {'collected_at': info['collected_at'], 'timezone': info.get('timezone'), 'rows': rows}
                        if old != entry:
                            self.days[day][key] = entry
                            self.dirty.add(day)
            archived = [(day, name, entry) for day, name, entry in self._selected()
                        if (day, name) not in covered and entry['rows']]
            if self.dirty and self.last_saved is None and self.worker and not self.initial_requested:
                self.initial_requested = True
                self.wake.put_nowait('first-checkpoint')
        merged = dict(data, rows=list(data['rows']), hourly_rows=list(data['hourly_rows']),
                      sources={name: dict(info) for name, info in data['sources'].items()},
                      warnings=list(data.get('warnings', [])))
        for day, name, entry in archived:
            rows = [dict(row, date=day, host=name) for row in entry['rows']]
            merged['rows'].extend(rows)
            merged['hourly_rows'].extend(dict(row, hour='Unknown hour') for row in rows)
            info = merged['sources'].setdefault(name, {'status': 'unavailable', 'collected_at': None, 'timezone': None})
            info['archive_collected_at'] = max(info.get('archive_collected_at') or '', entry['collected_at'])
            if not info['collected_at']:
                info.update(collected_at=info['archive_collected_at'], timezone=entry['timezone'], status='archived')
        names = sorted({name for _, name, _ in archived})
        if names:
            merged['warnings'] = [warning for warning in merged['warnings']
                                  if not any(warning.startswith(name + ':') for name in names)]
            for name in names:
                info = merged['sources'][name]
                state = 'offline; ' if info['status'] != 'fresh' else ''
                merged['warnings'].append(f'{name}: {state}daily archive included (last counts {info["archive_collected_at"]}). '
                                          'Unrecorded activity and archived hourly/session detail are unavailable.')
            if any(merged['sources'][name]['status'] != 'fresh' for name in names):
                merged['warnings'].append('Offline daily totals are approximate; copied records cannot be rechecked until their machine reconnects.')
        return merged

    def flush(self):
        with self.lock:
            pending = {day: {key: dict(entry) for key, entry in self.days[day].items()} for day in self.dirty}
        if not pending:
            return
        saved_at = time.time()
        for day, entries in sorted(pending.items()):
            record = {'version': 1, 'date': day, 'saved_at': saved_at,
                      'sources': [dict(entry, name=key[0], identity=key[1]) for key, entry in sorted(entries.items())]}
            body = (json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n').encode()
            if len(body) > MAX_DAY_BYTES:
                raise ValueError('Daily accounting log exceeds its size limit')
            path = self.folder / (day + '.json')
            if path.is_symlink():
                raise ValueError('Daily accounting log must not be a symlink')
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.folder, prefix='.daily-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(body)
                os.replace(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            with self.lock:
                self.bytes_written += len(body)
                if self.days[day] == entries:
                    self.dirty.discard(day)
        with self.lock:
            self.last_saved = saved_at
            self.save_batches += 1
            self.error = None

    def _writer(self):
        deadline = time.monotonic() + SAVE_INTERVAL
        while not self.stopping.is_set():
            try:
                self.wake.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                pass
            if self.stopping.is_set():
                break
            try:
                self.flush()
            except (OSError, ValueError) as error:
                self.error = 'Daily usage could not be saved; current RAM totals remain available.'
                print(f'Daily archive save failed: {error}', file=sys.stderr, flush=True)
            deadline = time.monotonic() + SAVE_INTERVAL
        try:
            self.flush()
        except (OSError, ValueError) as error:
            self.error = 'Daily archive shutdown save failed.'
            print(f'Daily archive shutdown save failed: {error}', file=sys.stderr, flush=True)

    def status(self):
        with self.lock:
            return {'enabled': True, 'save_interval_seconds': SAVE_INTERVAL,
                    'bytes_written': self.bytes_written, 'save_batches': self.save_batches,
                    'last_saved_at': self.last_saved, 'error': self.error}

    def close(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.worker:
            try:
                self.wake.put_nowait('stop')
            except queue.Full:
                pass  # A queued initial checkpoint also wakes shutdown.
            self.worker.join(timeout=10)
            if self.worker.is_alive():
                raise RuntimeError('Daily archive writer did not stop')
        else:
            self.flush()
