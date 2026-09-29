"""Safe, read-only view of planning evidence; no CSV/complaint access."""
import json
from datetime import date
from pathlib import Path

FIELD_KEYS = ('original_text', 'language', 'category', 'event_time', 'resolution', 'record_id')
FIELD_STATUSES = {'present_in_header', 'candidate_unverified', 'not_observed'}


class CoverageUnavailable(Exception):
    """Missing, invalid or inconsistent evidence must never become empty coverage."""


def require(condition):
    if not condition:
        raise CoverageUnavailable()


def load_coverage(planning_dir: Path, regions: list[dict]) -> dict:
    try:
        inventory = json.loads((planning_dir / 'data_inventory.json').read_text(encoding='utf-8'))
        matrix = json.loads((planning_dir / 'data_matrix.json').read_text(encoding='utf-8'))
        return build_coverage(inventory, matrix, regions)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CoverageUnavailable() from exc


def build_coverage(inventory, matrix, regions):
    for evidence in (inventory, matrix):
        require(isinstance(evidence['as_of'], str))
        date.fromisoformat(evidence['as_of'])
    expected = inventory['expected_regions']
    names = {r['name_ru']: r for r in regions}
    require(len(expected) == len(set(expected)) and set(expected) == set(names))
    source_rows = matrix['field_matrix']
    require(len(source_rows) == len(expected))
    rows_by_name = {row['region']: row for row in source_rows}
    require(set(rows_by_name) == set(expected))
    files = inventory['organizer_source']['files']
    file_ids = [f['file_id'] for f in files]
    require(all(isinstance(i, str) and i for i in file_ids))
    require(len(file_ids) == len(set(file_ids)))
    require(all(f['region'] in names for f in files))
    result = []
    for name in expected:
        row = rows_by_name[name]
        regional_files = [f for f in files if f['region'] == name]
        supplied = bool(regional_files)
        source = row['source']
        require((source is not None) == supplied)
        if supplied:
            ids = source.get('file_ids', [source.get('file_id')])
            require(len(ids) == len(regional_files) and set(ids) == {f['file_id'] for f in regional_files})
        fields = {}
        for key in FIELD_KEYS:
            field = row['fields'][key]
            if field is not None:
                require(supplied and field['status'] in FIELD_STATUSES)
                require(isinstance(field['columns'], list))
                require(all(isinstance(c, str) for c in field['columns']))
                fields[key] = {'status': field['status'], 'columns': field['columns']}
            else:
                fields[key] = None
        # ponytail: only reviewed complete local structural counts may be exposed.
        # Rows are not unique complaints; partial audits do not imply regional totals.
        counts = []
        for file in regional_files:
            audit = file.get('local_profile')
            if audit is None:
                continue
            count = audit['parsed_data_records']
            require(type(count) is int and count >= 0)
            require(audit['header_matches_inventory'] is True)
            require(audit['size_bytes'] == file['size_bytes'])
            if audit['complete_record_count_verified'] is True:
                counts.append(count)
        count = sum(counts) if supplied and len(counts) == len(regional_files) else None
        require(row['period'] is None or isinstance(row['period'], str))
        require(row['last_updated'] is None or isinstance(row['last_updated'], str))
        result.append({
            'region_id': names[name]['id'], 'name_ru': name,
            'name_kk': names[name]['name_kk'],
            'supply_status': 'supplied' if supplied else 'missing',
            'file_count': len(regional_files), 'record_count': count,
            'count_unit': 'csv_records_not_unique_complaints',
            'period': row['period'], 'last_updated': row['last_updated'],
            'fields': fields,
        })
    supplied_count = sum(r['supply_status'] == 'supplied' for r in result)
    return {
        'evidence_kind': 'organizer_metadata',
        'as_of': {'inventory': inventory['as_of'], 'matrix': matrix['as_of']},
        'sources': ['planning/data_inventory.json', 'planning/data_matrix.json'],
        'summary': {'required_regions': len(expected), 'supplied_regions': supplied_count,
                    'missing_regions': len(expected) - supplied_count, 'supplied_files': len(files)},
        'regions': result,
    }
