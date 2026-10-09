"""Minute aggregates for the meter; seconds stay in RAM, journal writes are batched."""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
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

RANGES = (600, 3600, 10800, 21600, 43200, 86400, 259200, 604800, 1209600, 2419200)
RETENTION = RANGES[-1]
SAVE_INTERVAL = 900
USAGE_KNOWN, USAGE_PARTIAL, DAY_ONLY = 1, 2, 4


class MeterHistory:
    """One bounded numeric store shared by live readings and existing accounting."""
    def __init__(self, folder=None, *, start_writer=True):
        self.folder = Path(folder) if folder else None
        self.lock = threading.RLock()
        self.rows, self.dirty = {}, {}
        self.receipt_rows, self.receipt_dirty, self.receipt_seconds = {}, {}, {}
        self.seconds = deque(maxlen=600)
        self.fine_rates, self.fine_usage = {}, {}
        self.previous = None
        self.storage_error = None
        self.bytes_written = 0
        self.save_batches = 0
        self.last_saved = None
        self.wake = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.worker = None
        if self.folder:
            if self.folder.is_symlink():
                raise ValueError('Meter history folder must not be a symlink')
            self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._restore()
            if start_writer:
                self.worker = threading.Thread(target=self._writer, name='meter-history-writer')
                self.worker.start()

    @staticmethod
    def _key(row):
        return (bool(row[8] & DAY_ONLY), row[1])

    @staticmethod
    def _valid(row):
        return (isinstance(row, list) and len(row) == 9 and type(row[0]) is int and row[0] == 1 and
                type(row[1]) is int and row[1] % 60 == 0 and
                all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in row[2:7]) and
                row[3] <= 60.001 and row[5] <= 60.001 and row[6] <= 60.001 and
                type(row[7]) is int and 0 <= row[7] <= 2**63 - 1 and
                type(row[8]) is int and 0 <= row[8] <= 7)

    def _restore(self):
        cutoff = int((time.time() - RETENTION) // 60) * 60
        for path in sorted(self.folder.glob('*.jsonl')):
            match = re.fullmatch(r'(receipts-)?\d{4}-\d{2}-\d{2}\.jsonl', path.name)
            if not match:
                continue
            receipts = bool(match.group(1))
            if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('Invalid or oversized meter history journal')
            body = path.read_bytes()
            for index, line in enumerate(body.splitlines(keepends=True)):
                if not line.endswith(b'\n'):
                    self.storage_error = 'An incomplete history tail was excluded.'
                    print('Meter history: incomplete journal tail excluded.', file=sys.stderr, flush=True)
                    break
                row = json.loads(line)
                if not (self._valid_receipt(row) if receipts else self._valid(row)):
                    raise ValueError('Invalid meter history record at line ' + str(index + 1))
                if row[1] >= cutoff:
                    if receipts:
                        self.receipt_rows[row[1]] = row
                    else:
                        self.rows[self._key(row)] = row

    @staticmethod
    def _valid_receipt(row):
        return (isinstance(row, list) and len(row) == 5 and type(row[0]) is int and row[0] == 2 and
                type(row[1]) is int and row[1] % 60 == 0 and
                type(row[2]) is int and 0 <= row[2] <= 2**63 - 1 and
                all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 60
                    for value in row[3:5]))

    def observe_receipts(self, updates, now=None):
        """Absolute bins from ReceivedOutput; reconcile copies without recounting."""
        now = time.time() if now is None else now
        with self.lock:
            for item in updates:
                stamp, tokens = item['start'], item['output_tokens']
                observed, partial = item['observed'], item['partial']
                if (type(stamp) is not int or type(tokens) is not int or not 0 <= tokens <= 2**63 - 1 or
                        type(observed) is not bool or type(partial) is not bool):
                    raise ValueError('Invalid receipt history bin')
                value = (tokens, observed, partial)
                old = self.receipt_seconds.get(stamp, (0, False, False))
                if stamp in self.receipt_seconds and old == value:
                    continue
                self.receipt_seconds[stamp] = value
                minute = stamp // 60 * 60
                row = list(self.receipt_rows.get(minute, [2, minute, 0, 0, 0]))
                row[2] += tokens - old[0]
                row[3] += int(observed) - int(old[1])
                row[4] += int(partial) - int(old[2])
                if not self._valid_receipt(row):
                    raise ValueError('Receipt history correction exceeds its minute')
                self.receipt_rows[minute] = row
                self.receipt_dirty[minute] = row
            # Retain completed-bin boundaries plus the 600-second reconciliation
            # window. Insertion order preserves old corrections across a clock
            # adjustment; wall-clock age must not discard their previous values.
            for stamp in list(self.receipt_seconds)[:max(0, len(self.receipt_seconds) - 602)]:
                self.receipt_seconds.pop(stamp)
            if len(self.receipt_rows) > RETENTION // 60 + 100:
                cutoff = int((now - RETENTION) // 60) * 60
                for stamp in [stamp for stamp in self.receipt_rows if stamp < cutoff]:
                    self.receipt_rows.pop(stamp)
                    self.receipt_dirty.pop(stamp, None)

    def _receipt_snapshot(self, begin, end, step, now):
        buckets = [{'start': stamp, 'output_tokens': None, 'receipt_tps': None,
                    'observed_seconds': 0, 'partial': False, 'coarse_receipts': False}
                   for stamp in range(begin, end, step)]
        fine = defaultdict(list)
        for stamp, value in self.receipt_seconds.items():
            fine[stamp // 60 * 60].append((stamp, value))
        for minute, row in self.receipt_rows.items():
            if minute + 60 <= begin or minute >= end:
                continue
            detail = fine.get(minute, [])
            use_detail = (detail and sum(value[0] for _, value in detail) == row[2] and
                          sum(value[1] for _, value in detail) == row[3] and
                          sum(value[2] for _, value in detail) == row[4])
            if use_detail:
                parts = [(stamp, 1, value[0], int(value[1]), int(value[2]), False)
                         for stamp, value in detail]
            else:
                # A restored current-minute record must not spread its known
                # receipts into future time. Complete older minutes use 60 s.
                span = min(60, max(1, int(now - minute), row[3], row[4]))
                parts = [(minute, span, row[2], row[3], row[4], step < 60)]
            for stamp, span, tokens, observed, partial, coarse in parts:
                first = max(0, (stamp - begin) // step)
                last = min(len(buckets), math.ceil((stamp + span - begin) / step))
                for index in range(first, last):
                    bucket = buckets[index]
                    overlap = min(stamp + span, bucket['start'] + step) - max(stamp, bucket['start'])
                    fraction = overlap / span
                    if tokens or observed:
                        bucket['output_tokens'] = (bucket['output_tokens'] or 0) + tokens * fraction
                    bucket['observed_seconds'] += observed * fraction
                    bucket['coarse_receipts'] |= coarse
                    if partial:
                        bucket['partial'] = True
        for bucket in buckets:
            tokens = bucket['output_tokens']
            if tokens is not None:
                bucket['receipt_tps'] = tokens / step
            # Missing time remains explicit; the denominator is always the full
            # bucket duration, just as the usage average always divides by 300.
            bucket['partial'] |= bucket['observed_seconds'] < step
        return buckets

    def _put(self, row):
        key = self._key(row)
        if self.rows.get(key) != row:
            self.rows[key] = row
            self.dirty[key] = row

    @staticmethod
    def _integrate(row, value, span, limit):
        for metric, integral, covered in (('total_tps', 2, 3), ('average_tps', 4, 5)):
            rate = value.get(metric)
            if type(rate) in (float, int) and math.isfinite(rate) and rate >= 0:
                add = min(span, max(0, limit - row[covered]))
                row[integral] += rate * add
                row[covered] += add
        if value.get('status') != 'complete':
            row[6] = min(limit, row[6] + span)

    def observe(self, snapshot, wall=None, monotonic=None):
        wall = time.time() if wall is None else wall
        monotonic = time.monotonic() if monotonic is None else monotonic
        with self.lock:
            self.seconds.append((wall, snapshot.get('total_tps'), snapshot.get('average_tps'),
                                 snapshot.get('status') != 'complete'))
            prior = self.previous
            self.previous = (wall, monotonic, snapshot)
            if prior is None:
                return
            elapsed = monotonic - prior[1]
            # Sleep, suspension and missed heartbeats are gaps, never interpolated activity.
            if not 0 < elapsed <= 2.5 or abs(wall - prior[0] - elapsed) > 1:
                return
            # Keep finer integration in RAM only. The existing journal still
            # contains one-minute records and uses the same 15-minute batching.
            for interval in (60, 5):
                start = wall - elapsed
                while start < wall:
                    stamp = int(start // interval) * interval
                    stored = self.rows.get((False, stamp)) if interval == 60 else self.fine_rates.get(stamp)
                    row = list(stored or [1, stamp, 0, 0, 0, 0, 0, 0, 0])
                    span = min(wall, stamp + interval) - start
                    self._integrate(row, prior[2], span, interval)
                    if interval == 60:
                        self._put(row)
                    else:
                        self.fine_rates[stamp] = row
                    start += span
            if len(self.fine_rates) > 733:
                cutoff = int((wall - 3600 - 60) // 5) * 5
                self.fine_rates = {stamp: row for stamp, row in self.fine_rates.items() if stamp >= cutoff}
            if len(self.rows) > RETENTION // 60 + 100:
                cutoff = int((wall - RETENTION) // 60) * 60
                for key in [key for key in self.rows if key[1] < cutoff]:
                    self.rows.pop(key)
                    self.dirty.pop(key, None)

    def account(self, projection):
        """Absolute counts from the existing deduplicated accounting, not integrated TPS."""
        minutes = projection['minutes']
        begin, end = projection['start'], projection['end']
        known, partial = projection['available'], projection['partial']
        if not known:
            return  # A failed collection cannot erase previously accounted usage.
        fine = projection.get('fine')
        if fine is not None:
            if not isinstance(fine, dict):
                raise ValueError('Invalid short-term usage projection')
            start, finish, counts = fine.get('start'), fine.get('end'), fine.get('counts')
            if (type(start) is not int or type(finish) is not int or start % 5 or finish % 5 or
                    not 0 < finish - start <= 3660 or not isinstance(counts, dict) or len(counts) > 732):
                raise ValueError('Invalid short-term usage projection')
            allowed = {str(stamp) for stamp in range(start, finish, 5)}
            if (not counts.keys() <= allowed or
                    any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in counts.values())):
                raise ValueError('Invalid short-term token count')
        with self.lock:
            for minute in range(begin, end, 60):
                row = list(self.rows.get((False, minute), [1, minute, 0, 0, 0, 0, 0, 0, 0]))
                tokens = minutes.get(str(minute), 0)
                if not (partial and row[8] & USAGE_KNOWN and tokens <= row[7]):
                    row[7] = max(row[7], tokens) if partial else tokens
                    row[8] = USAGE_KNOWN | (USAGE_PARTIAL if partial else 0)
                self._put(row)
            if fine is not None:
                for stamp in range(start, finish, 5):
                    tokens = counts.get(str(stamp), 0)
                    previous = self.fine_usage.get(stamp)
                    if partial and previous:
                        tokens = max(tokens, previous[0])
                    self.fine_usage[stamp] = (tokens, partial)
                self.fine_usage = {stamp: row for stamp, row in self.fine_usage.items() if stamp >= start}
            days = set()
            for day, tokens in projection['day_only'].items():
                stamp = int(datetime.strptime(day, '%Y-%m-%d').timestamp())
                days.add(stamp)
                if partial:
                    tokens = max(tokens, self.rows.get((True, stamp), [0] * 9)[7])
                self._put([1, stamp, 0, 0, 0, 0, 0, tokens, DAY_ONLY | USAGE_KNOWN | USAGE_PARTIAL])
            if not partial:
                # A subsequently detailed import replaces a coarse daily rollup.
                for key in list(self.rows):
                    if key[0] and begin <= key[1] < end and key[1] not in days:
                        row = list(self.rows[key])
                        row[7] = 0
                        self._put(row)

    def snapshot(self, period, now=None):
        if period not in RANGES:
            raise ValueError('Unsupported meter history range')
        now = time.time() if now is None else now
        step = period // 120
        alignment = min(60, step)
        end = int(now // alignment) * alignment + alignment
        begin = end - period
        buckets = [[stamp, 0., 0., 0., 0., 0, False, False, False, False] for stamp in range(begin, end, step)]
        untimed = 0
        with self.lock:
            fine_minutes = {stamp // 60 * 60 for stamp in self.fine_rates} if step <= 60 else set()
            for (day_only, stamp), row in self.rows.items():
                if day_only:
                    next_day = (datetime.fromtimestamp(stamp) + timedelta(days=1)).timestamp()
                    if begin <= stamp and next_day <= now:
                        untimed += row[7]
                    continue
                if stamp + 60 <= begin or stamp >= end:
                    continue
                first = max(0, (stamp - begin) // step)
                last = min(len(buckets), math.ceil((stamp + 60 - begin) / step))
                for index in range(first, last):
                    bucket = buckets[index]
                    fraction = (min(stamp + 60, bucket[0] + step) - max(stamp, bucket[0])) / 60
                    if stamp not in fine_minutes:
                        for field in range(1, 5):
                            bucket[field] += row[field + 1] * fraction
                        bucket[8] |= step < 60 and row[3] > 0
                    bucket[7] |= row[6] > 0 or bool(row[8] & USAGE_PARTIAL)
                if row[8] & USAGE_KNOWN:
                    detail = [self.fine_usage.get(second) for second in range(stamp, stamp + 60, 5)]
                    # Do not replace larger retained counts with an incomplete
                    # source. Exact receipt timestamps stay unavailable until
                    # the detailed counts reconcile with the minute total.
                    use_detail = any(value is not None for value in detail) and sum(value[0] for value in detail if value) == row[7]
                    if use_detail:
                        for second, value in zip(range(stamp, stamp + 60, 5), detail):
                            if value is None or not begin <= second < end:
                                continue
                            bucket = buckets[(second - begin) // step]
                            bucket[5] += value[0]
                            bucket[6] = True
                            bucket[7] |= value[1]
                    elif stamp >= begin:
                        bucket = buckets[(stamp - begin) // step]
                        bucket[5] += row[7]
                        bucket[6] = True
                        bucket[9] |= step < 60
            # Override coarse minute means only where actual finer observations
            # exist. Older means retain an explicit coarse-resolution label.
            detailed = {}
            for stamp, row in self.fine_rates.items():
                if not begin <= stamp < end:
                    continue
                index = (stamp - begin) // step
                target = detailed.setdefault(index, [0., 0., 0., 0., False])
                for field in range(4):
                    target[field] += row[field + 2]
                target[4] |= row[6] > 0
            for index, values in detailed.items():
                bucket = buckets[index]
                # For larger ranges use the complete persisted minute integral;
                # a short RAM tail must not erase the rest of a wide time bucket.
                if step > 60:
                    continue
                bucket[1:5] = values[:4]
                bucket[7] |= values[4]
                bucket[8] = False
            storage = {'enabled': bool(self.folder), 'save_interval_seconds': SAVE_INTERVAL,
                       'bytes_written': self.bytes_written, 'save_batches': self.save_batches,
                       'last_saved_at': self.last_saved, 'error': self.storage_error}
            receipts = self._receipt_snapshot(begin, end, step, now)
            # Detailed rates are optional RAM data. Keep usage counts at their
            # recorded minute grain; never spread a late count into fake seconds.
            fine = None
            if period == 600:
                rate_end = int(now) + 1
                rate_start = rate_end - period
                fine = {stamp: {'start': stamp, 'total_tps': None,
                               'average_tps': None, 'partial': True}
                        for stamp in range(rate_start, rate_end)}
                for wall, total, average, partial in self.seconds:
                    stamp = int(wall)
                    if rate_start <= stamp < rate_end:
                        fine[stamp] = {'start': stamp, 'total_tps': total,
                                      'average_tps': average, 'partial': partial}
        output = [{'start': row[0], 'total_tps': row[1] / row[2] if row[2] else None,
                   'average_tps': row[3] / row[4] if row[4] else None,
                   'tokens': row[5] if row[6] else None, 'observed_seconds': row[2],
                   'coarse_rate': row[8], 'coarse_tokens': row[9],
                   'partial': row[7] or row[2] < min(step, max(0, now - row[0])) - 1}
                  for row in buckets]
        seconds = sum(row[2] for row in buckets)
        average_seconds = sum(row[4] for row in buckets)
        token_available = any(row[6] for row in buckets)
        result = {'version': 1, 'range_seconds': period, 'bucket_seconds': step,
                'receipt_basis': 'reported_output', 'receipt_buckets': receipts,
                'start': begin, 'end': end, 'buckets': output,
                'total_tps': sum(row[1] for row in buckets) / seconds if seconds else None,
                'average_tps': sum(row[3] for row in buckets) / average_seconds if average_seconds else None,
                'total_tokens': sum(row[5] for row in buckets) + untimed if token_available or untimed else None,
                'day_only_tokens': untimed, 'observed_seconds': seconds,
                'partial': bool(untimed or any(row['partial'] for row in output)), 'storage': storage}
        if fine is not None:
            result.update(rate_start=rate_start, rate_end=rate_end,
                          rate_bucket_seconds=1, rate_buckets=list(fine.values()))
        return result

    def flush(self):
        if not self.folder:
            return
        with self.lock:
            pending = {('usage', key): row for key, row in self.dirty.items()}
            pending.update({('receipts', key): row for key, row in self.receipt_dirty.items()})
        if not pending:
            return
        groups = defaultdict(list)
        for (kind, key), row in pending.items():
            day = datetime.fromtimestamp(row[1], timezone.utc).strftime('%Y-%m-%d')
            groups[kind, day].append((key, row))
        for (kind, day), records in sorted(groups.items()):
            path = self.folder / (('receipts-' if kind == 'receipts' else '') + day + '.jsonl')
            rows = self.receipt_rows if kind == 'receipts' else self.rows
            dirty = self.receipt_dirty if kind == 'receipts' else self.dirty
            if path.is_symlink():
                raise ValueError('Meter journal must not be a symlink')
            # Remove only a torn final append. Completed records remain intact.
            if path.exists():
                with path.open('rb+') as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    if size:
                        stream.seek(size - 1)
                        if stream.read(1) != b'\n':
                            stream.seek(0)
                            body = stream.read()
                            stream.truncate(body.rfind(b'\n') + 1)
            data = ''.join(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n'
                           for _, row in records).encode()
            if path.exists() and path.stat().st_size + len(data) > 2 * 1024 * 1024:
                # Compact only oversized journals, retaining the latest minute values.
                with self.lock:
                    records = [(key, row) for key, row in rows.items()
                               if datetime.fromtimestamp(row[1], timezone.utc).strftime('%Y-%m-%d') == day]
                data = ''.join(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n'
                               for _, row in sorted(records)).encode()
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.folder, prefix='.compact-', delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(data)
                    os.replace(temporary, path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
            else:
                with path.open('ab') as stream:
                    path.chmod(0o600)
                    stream.write(data)
            with self.lock:
                self.bytes_written += len(data)
                for key, row in records:
                    if dirty.get(key) == row:
                        dirty.pop(key)
        cutoff_day = datetime.fromtimestamp(time.time() - RETENTION - 86400, timezone.utc).strftime('%Y-%m-%d')
        for path in self.folder.glob('*.jsonl'):
            if (re.fullmatch(r'(receipts-)?\d{4}-\d{2}-\d{2}\.jsonl', path.name) and
                    path.stem.removeprefix('receipts-') < cutoff_day):
                path.unlink()
        with self.lock:
            self.save_batches += 1
            self.last_saved = time.time()
            self.storage_error = None

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
                self.storage_error = 'Meter history could not be saved; RAM readings remain available.'
                print(f'Meter history save failed: {error}', file=sys.stderr, flush=True)
            deadline = time.monotonic() + SAVE_INTERVAL
        try:
            self.flush()
        except (OSError, ValueError) as error:
            self.storage_error = 'Meter history shutdown save failed.'
            print(f'Meter history shutdown save failed: {error}', file=sys.stderr, flush=True)

    def close(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.worker:
            self.wake.put_nowait('stop')
            self.worker.join(timeout=10)
            if self.worker.is_alive():
                raise RuntimeError('Meter history writer did not stop')
        else:
            self.flush()
