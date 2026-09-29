"""Coverage regression tests use metadata only; never open organizer CSVs."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import REGIONS
from app import get_data_coverage
from fastapi import HTTPException
from data_coverage import CoverageUnavailable, load_coverage


class CoverageChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = json.loads((ROOT / 'planning/data_inventory.json').read_text(encoding='utf-8'))
        for file in self.inventory['organizer_source']['files']:
            file.pop('local_profile', None)
        self.matrix = json.loads((ROOT / 'planning/data_matrix.json').read_text(encoding='utf-8'))
        self.write_sources()

    def write_sources(self):
        for name, data in [('data_inventory', self.inventory), ('data_matrix', self.matrix)]:
            (self.root / (name + '.json')).write_text(json.dumps(data), encoding='utf-8')

    def test_real_metadata_separate_from_demo(self):
        data = load_coverage(self.root, REGIONS)
        self.assertEqual(data['evidence_kind'], 'organizer_metadata')
        self.assertEqual(data['summary'], {'required_regions': 20, 'supplied_regions': 7,
                                         'missing_regions': 13, 'supplied_files': 8})
        self.assertEqual(len(data['regions']), 20)
        self.assertTrue(all(r['record_count'] is None for r in data['regions']))
        self.assertTrue(all(r['period'] is None for r in data['regions']))

    def test_almaty_city_is_not_almaty_region(self):
        rows = {r['region_id']: r for r in load_coverage(self.root, REGIONS)['regions']}
        self.assertEqual(rows['KZ-ALA']['supply_status'], 'missing')
        self.assertEqual(rows['KZ-ALM']['supply_status'], 'supplied')
        self.assertIsNone(rows['KZ-ALA']['fields']['original_text'])
        self.assertEqual(rows['KZ-AKM']['fields']['original_text']['status'], 'candidate_unverified')
        self.assertEqual(rows['KZ-PAV']['file_count'], 2)

    def test_reviewed_local_counts_have_explicit_unit(self):
        data = load_coverage(ROOT / 'planning', REGIONS)
        self.assertEqual(sum(r['record_count'] or 0 for r in data['regions']), 1036858)
        self.assertEqual(sum(r['record_count'] is None for r in data['regions']), 13)
        self.assertTrue(all(r['count_unit'] == 'csv_records_not_unique_complaints' for r in data['regions']))
        self.assertTrue(all(r['period'] is None for r in data['regions']))

    def test_partial_audit_does_not_become_a_regional_total(self):
        file = next(f for f in self.inventory['organizer_source']['files'] if f['region'] == 'Павлодарская область')
        file['local_profile'] = {'parsed_data_records': 10, 'header_matches_inventory': True,
                                 'size_bytes': file['size_bytes'], 'complete_record_count_verified': True}
        self.write_sources()
        row = next(r for r in load_coverage(self.root, REGIONS)['regions'] if r['region_id'] == 'KZ-PAV')
        self.assertIsNone(row['record_count'])
        file['local_profile']['parsed_data_records'] = True
        self.write_sources()
        with self.assertRaises(CoverageUnavailable):
            load_coverage(self.root, REGIONS)

    def test_endpoint_hides_error_details(self):
        with patch('app.load_coverage', side_effect=CoverageUnavailable('private-path-and-values')):
            with self.assertRaises(HTTPException) as error:
                get_data_coverage()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.detail, {'error': 'coverage_unavailable'})

    def test_counts_derive_from_files_not_cached_totals(self):
        # A changed source requires matching matrix evidence, not hand-editing UI counts.
        target = next(r for r in self.matrix['field_matrix'] if r['region'] == 'город Алматы')
        target['source'] = {'file_id': 'test-file'}
        target['fields'] = copy.deepcopy(self.matrix['field_matrix'][1]['fields'])
        self.inventory['organizer_source']['files'].append({'region': 'город Алматы', 'file_id': 'test-file'})
        self.write_sources()
        self.assertEqual(load_coverage(self.root, REGIONS)['summary']['supplied_regions'], 8)

    def test_mismatch_rejected_instead_of_inventing_coverage(self):
        self.inventory['organizer_source']['files'].pop()
        self.write_sources()
        with self.assertRaises(CoverageUnavailable):
            load_coverage(self.root, REGIONS)

    def test_duplicate_region_and_unknown_status_fail_closed(self):
        self.matrix['field_matrix'].append(copy.deepcopy(self.matrix['field_matrix'][0]))
        self.write_sources()
        with self.assertRaises(CoverageUnavailable):
            load_coverage(self.root, REGIONS)
        self.matrix['field_matrix'].pop()
        self.matrix['field_matrix'][1]['fields']['original_text']['status'] = 'invented_verified'
        self.write_sources()
        with self.assertRaises(CoverageUnavailable):
            load_coverage(self.root, REGIONS)

    def test_missing_and_malformed_source_are_unavailable(self):
        path = self.root / 'data_matrix.json'
        for contents in ['{invalid', '[]', '{}']:
            path.write_text(contents, encoding='utf-8')
            with self.assertRaises(CoverageUnavailable):
                load_coverage(self.root, REGIONS)
        path.unlink()
        with self.assertRaises(CoverageUnavailable):
            load_coverage(self.root, REGIONS)

    def test_malformed_provenance_date_is_unavailable(self):
        for value in [None, {}, 'not-a-date']:
            self.inventory['as_of'] = value
            self.write_sources()
            with self.assertRaises(CoverageUnavailable):
                load_coverage(self.root, REGIONS)


def check_api(base_url, request):
    status, data = request(base_url + '/api/data-coverage')
    assert status == 200 and len(data['regions']) == 20
    status, selected = request(base_url + '/api/data-coverage?region_id=KZ-ALA')
    assert status == 200 and len(selected['regions']) == 1
    assert selected['regions'][0]['supply_status'] == 'missing'
    assert selected['regions'][0]['record_count'] is None
    assert selected['summary'] == data['summary'], 'Summary is explicitly national metadata'
    for query in ['UNKNOWN', '']:
        status, _ = request(base_url + '/api/data-coverage?region_id=' + query)
        assert status == 422


if __name__ == '__main__':
    unittest.main()
