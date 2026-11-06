import copy
import unittest

from experiments.news_processing import bulk_dataset as bulk, path_first_dataset as paths
from tests import test_news_path_first_dataset as fixtures


class ProcessEvidenceTests(unittest.TestCase):
    def test_process_names_do_not_turn_parallel_inputs_into_outputs(self):
        for left, right, text in [
            ('焦煤', '焦炭', '炼焦企业同时采购焦煤和焦炭。'),
            ('大豆', '豆粕', '浸提企业同时采购大豆和豆粕。'),
        ]:
            with self.subTest(text=text):
                self.assertFalse(paths._link(left, right, 'INPUT_OUTPUT', [
                    {'text': text, 'field': 'industry_background', 'owner': 'industry'}]))

    def test_catalogue_definition_keeps_headers_and_conversion_direction(self):
        definition = '大豆经直接溶剂浸提取油后获得的副产品。'
        table = '序号 | 原料名称 | 特征描述 | 2.3.14 | 豆粕 | ' + definition
        rows = [{'text': table, 'field': 'industry_background', 'owner': 'industry'}]
        self.assertTrue(paths._link('大豆', '豆粕', 'INPUT_OUTPUT', rows))
        self.assertFalse(paths._link('豆粕', '大豆', 'INPUT_OUTPUT', rows))
        rows[0]['text'] = '豆粕 | ' + definition
        self.assertFalse(paths._link('大豆', '豆粕', 'INPUT_OUTPUT', rows))

    def test_specific_coking_and_extraction_processes_can_support_a_material_link(self):
        for source, product, quote in [
            ('焦煤', '焦炭', '对焦煤进行炼焦处理可生成焦炭、焦炉煤气及其他化学产品。'),
            ('大豆', '豆粕', '豆粕由大豆经直接溶剂浸提取油后获得。'),
        ]:
            with self.subTest(source=source):
                f=fixtures.PathFirstDatasetTests();f.setUp()
                article,catalog,pair=copy.deepcopy(f.a),copy.deepcopy(f.c),copy.deepcopy(f.p)
                article['text']=source+'价格上涨3%。'
                article['packet']=bulk.packet('P1',article['text'])
                article['fact'].update(focus_quote=article['text'],focus_product=source)
                catalog['A']['evidence'][0]['text']='本公司采购'+product+'用于生产。'
                catalog['S']['industry_evidence'][0]['text']=quote
                article['industry_context'][1]['text']=quote
                pair.update(source_product=source,endpoint_product=product,
                            why=source+'经'+product+'影响目标采购成本，传导尚待确认。')
                pair['reason']=pair.pop('why')
                pair['intermediates'][0].update(input_product=source,product=product,bridge_ids=['IGS'])
                self.assertEqual(paths.derive_pair(article,catalog,pair)['relation'],'DISTANT')
                # Listing two materials without this process remains insufficient.
                unrelated='同时列示'+source+'和'+product+'两种原料。'
                catalog['S']['industry_evidence'][0]['text']=unrelated
                article['industry_context'][1]['text']=unrelated
                with self.assertRaisesRegex(ValueError,'adjacent business link'):
                    paths.derive_pair(article,catalog,pair)
