import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.news_processing.__main__ import main
from experiments.news_processing.contract import QUESTIONS, input_digest
from experiments.news_processing.dataset import import_records
from experiments.news_processing.pipeline import render_review
from experiments.news_processing.storage import write_json, write_jsonl
from experiments.news_processing.teacher import PROMPTS
from tests.test_news_processing import record


HEADER = ['项目','本报告期','上年同期','本报告期比上年同期增减变动幅度']


class ReviewedEntryPointTests(unittest.TestCase):
    def financial(self, table):
        r = record();r['state']['news']['text'] = '证券代码600031 三一重工第一季度报告。单位：人民币百万元。' * 4
        r['state']['news']['market_rows'] = table
        r['material_provenance'] = {'kind':'official_pdf_fixed_excerpt','financial_table_page':1}
        return import_records([r],{'steel':'train'})[0]

    def test_cross_page_incomplete_metric_is_pending_despite_long_body(self):
        rows = [HEADER,['营业收入','22,133','33,402','-33.7%'],
                ['归属于母公司股东的基本加权','2.08','1.89','10.1%']]
        result = self.financial(rows)
        self.assertEqual(result['quality']['body'],'pending')
        self.assertTrue(result['material_quality_reasons'])

    def test_review_page_explains_pending_financial_table_quality(self):
        result=self.financial([HEADER,['归属于母公司股东的基本加权','2.08','1.89','10.1%']])
        self.assertIn('unresolved_or_incomplete_financial_metric',render_review([result],{},{}))

    def test_physical_column_misalignment_and_split_metric_are_pending(self):
        rows = [['', '', '', '本报告期','上年同期','','本报告期比上年同期增减',''],
                ['', '营业收入（元）','','29,893,668,691.43','36,061,237,090.13','-17.10%','',''],
                ['', '归属于上市公司股东的扣除','','-1,141,499,294.73','4,509,172,708.20','-125.32%','','']]
        self.assertEqual(self.financial(rows)['quality']['body'],'pending')

    def test_complete_financial_rows_with_unit_and_matching_columns_are_ready(self):
        rows = [HEADER,['营业收入','22,133','33,402','-33.7%'],
                ['归属于母公司股东的净利润','6,501','5,882','10.5%']]
        self.assertEqual(self.financial(rows)['quality']['body'],'ready')

    def test_full_per_share_metric_still_requires_its_own_unit(self):
        rows = [HEADER,['归属于母公司股东的基本加权平均每股收益','2.08','1.89','10.1%']]
        self.assertEqual(self.financial(rows)['quality']['body'],'pending')

    def test_complete_adjusted_comparative_multilevel_header_is_ready(self):
        rows = [['项目','本报告期','上年同期','','本报告期比上年同期增减变动幅度(%)'],
                ['','','调整前','调整后','调整后'],
                ['营业收入','24,042,362','21,049,036','21,049,036','14.22']]
        self.assertEqual(self.financial(rows)['quality']['body'],'ready')

    def test_cli_missing_prediction_still_exports_retained_review_page(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path);r=record();r['state']['news']['text'] *= 10;r['input_sha256']=input_digest(r);r['split']='train'
            write_jsonl(root/'inputs.jsonl',[r]);write_jsonl(root/'predictions.jsonl',[{'id':r['id'],'input_sha256':input_digest(r)}])
            import hashlib
            write_json(root/'protocol.json',{'input_sha256':hashlib.sha256((root/'inputs.jsonl').read_bytes()).hexdigest(),'questions':QUESTIONS,'prompts':PROMPTS})
            with patch('sys.argv',['news','replay','--root',str(root),'--predictions',str(root/'predictions.jsonl')]),contextlib.redirect_stdout(io.StringIO()):main()
            self.assertTrue((root/'human_review.html').exists())
            self.assertTrue(json.loads((root/'shadow_routes.jsonl').read_text())['retained'])


if __name__ == '__main__':unittest.main()
