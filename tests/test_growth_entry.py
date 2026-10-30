"""Count-contract checks for the current AOI 100+30 detector entry."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
KIT=Path('E:/competition/AOI_100plus30_kit/AOI_100plus30_kit')
sys.path.insert(0,str(KIT/'code'))
from aoi_detector import AoiDetector

class _Record:
    def __init__(self,path,label,group):self.path=path;self.label=label;self.group=group

class CountDetector(AoiDetector):
    def _lazy(self):
        class B:ImageRecord=_Record
        self._B=B
    def _extract(self,records):
        raise RuntimeError('COUNT_GATE_ACCEPTED')

class GrowthEntryTests(unittest.TestCase):
    def detector(self):
        return CountDetector(weights='unused-for-count-test',device='cpu')
    def folders(self,normal=101,defect=30):
        temp=tempfile.TemporaryDirectory();root=Path(temp.name)
        for role,count in [('normal',normal),('defect',defect)]:
            (root/role).mkdir()
            for i in range(count):(root/role/f'{i}.png').write_bytes(b'count-only')
        return temp,root
    def test_initial_rejects_nonstandard_count(self):
        temp,root=self.folders()
        with temp,self.assertRaises(SystemExit) as error:self.detector().fit(root/'normal',root/'defect')
        self.assertIn('exactly 100 normal + 30 defect',str(error.exception))
    def test_evolution_accepts_growing_count(self):
        temp,root=self.folders()
        with temp,self.assertRaisesRegex(RuntimeError,'COUNT_GATE_ACCEPTED'):
            self.detector().fit(root/'normal',root/'defect',allow_nonstandard=True)

if __name__=='__main__':unittest.main()
