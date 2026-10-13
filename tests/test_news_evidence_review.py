import json
import unittest


class EvidenceReviewTests(unittest.TestCase):
    def test_material_text_cannot_break_out_of_json_script(self):
        from experiments.news_processing.evidence_review import render
        malicious = '</script><script>alert("source")</script>'
        page = render({'pairs':1}, [{'id':malicious}], {})
        self.assertNotIn(malicious,page)
        encoded = page.split('<script type="application/json" id="data">',1)[1].split('</script>',1)[0]
        self.assertEqual(json.loads(encoded)['queue'][0]['id'],malicious)
        self.assertIn('textContent',page)


if __name__ == '__main__': unittest.main()
