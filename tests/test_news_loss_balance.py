import types
import unittest
from collections import Counter
from experiments.news_processing import loss_balance as w


class LossBalanceTests(unittest.TestCase):
    def rows(self):
        return [{'id':str(i),'expected':{'a':label}} for i,label in enumerate(['YES','NO','NO','NO'])]+[{'id':'b','expected':{'b':'ONE'}}]

    def test_mean_one_weights_balance_semantic_task_and_class(self):
        rows=self.rows();weights=w.row_weights(rows)
        self.assertAlmostEqual(sum(weights),len(rows))
        sums=Counter()
        for r,weight in zip(rows,weights):sums[next(iter(r['expected']))]+=weight
        self.assertAlmostEqual(sums['a'],sums['b']);self.assertGreater(weights[0],weights[1])

    def backend(self,fail=False):
        t=types.SimpleNamespace()
        t.encode_item=lambda tok,item,*args,**kwargs:item['target'][::-1]
        t.soft_ce_loss=lambda logits,target,mask:2.0
        def train(model,tok,items,config,device,max_len,head_max_len,on_epoch_end=None):
            results=[]
            for epoch in range(config.epochs):
                for item in reversed(items):
                    target=t.encode_item(tok,item,max_len,head_max_len,[1,0]);self.assertEqual(target,item['target'][::-1])
                    if fail:raise RuntimeError('training failed')
                    results.append(t.soft_ce_loss(None,target,None))
            return results
        t.train_model=train;return t

    def test_all_rows_once_per_epoch_weights_follow_items_despite_permutation(self):
        t=self.backend();items=[{'target':[1,0]},{'target':[0,1]}];config=types.SimpleNamespace(micro_batch=1,loss='soft-ce',epochs=2)
        trace=[];original=t.encode_item
        result=w.train_weighted(t,None,None,items,config,None,100,50,['a','b'],[0.5,1.5],trace=trace)
        self.assertEqual(result,[3.0,1.0,3.0,1.0]);self.assertEqual(Counter(x['id'] for x in trace),{'a':2,'b':2})
        self.assertIs(t.encode_item,original)

    def test_training_error_restores_backend_hooks(self):
        t=self.backend(fail=True);encode,loss=t.encode_item,t.soft_ce_loss
        with self.assertRaises(RuntimeError):
            w.train_weighted(t,None,None,[{'target':[1,0]}],types.SimpleNamespace(micro_batch=1,loss='soft-ce',epochs=1),None,100,50,['a'],[1.0])
        self.assertIs(t.encode_item,encode);self.assertIs(t.soft_ce_loss,loss)
