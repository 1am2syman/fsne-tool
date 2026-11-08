"""Hand-worked cases for transfer candidate discovery."""

import random
import unittest
from datetime import date
from typing import ClassVar

import polars as pl

from src.inventory_projection import deplete
from src.transfer_opportunities import STATUS, eligible, remaining, transform
from ui.transfer_data import build_payload


def frame(rows):
    keys = ["Town", "Material Code", "Batch code", "rsl_days",
            "total_closing_stock_kl", "exposure_kl", "exposure_bdt", "avg_sec_m"]
    d = {k: [r[i] for r in rows] for i, k in enumerate(keys)}
    d["material_desc"] = ["m"] * len(rows)
    d["Brand Name"] = ["b"] * len(rows)
    return pl.DataFrame(d, schema_overrides={"exposure_kl": pl.Float64,
                                             "exposure_bdt": pl.Float64,
                                             "avg_sec_m": pl.Float64})


class TransferTest(unittest.TestCase):
    rows: ClassVar[list] = [
        ("A", 1, "b1", 400, 8.0, 5.0, 500.0, 0.0),   # at-risk source, 100 d to 10M line
        ("B", 1, "b2", 50, 2.0, 0.0, 0.0, 3.0),      # destination with older stock
        ("C", 1, "b3", 500, 1.0, 0.0, 0.0, 0.0),     # no demand evidence
        ("B", 2, "b4", 400, 9.0, 0.0, 0.0, 3.0),     # other material: never paired
    ]

    def test_exact_material_pairs_and_absorbable_quantity(self):
        out = transform(frame(self.rows)).sort("dest_town")
        self.assertEqual(sorted(out["dest_town"]), ["B", "C"])
        self.assertTrue(set(out["Material Code"]) == {1})
        b = out.row(0, named=True)
        # B sells 3 KL/mo x 100 d / 30 = 10 KL; 2 KL older stock queues ahead -> room 8
        self.assertAlmostEqual(b["dest_room_kl"], 8.0)
        self.assertAlmostEqual(b["proposed_kl"], 5.0)  # capped by source exposure
        self.assertEqual(b["status"], STATUS)

    def test_rejections_and_older_stock(self):
        out = transform(frame(self.rows)).sort("dest_town")
        self.assertEqual(out["rejection_reason"].to_list(), [None, "no_demand_evidence"])
        self.assertEqual(out["dest_has_older_stock"].to_list(), [True, False])
        self.assertTrue(out.filter(pl.col("dest_town") == "C")["proposed_kl"].is_null().all())
        self.assertEqual(eligible(out).height, 1)

    def test_destination_room_is_shared_between_sources(self):
        rows = self.rows + [("D", 1, "b5", 400, 8.0, 5.0, 400.0, 0.0)]
        out = eligible(transform(frame(rows))).filter(pl.col("dest_town") == "B")
        self.assertAlmostEqual(float(out["proposed_kl"].sum()), 8.0)

    def test_below_300_can_improve_sell_through_without_erasing_breach(self):
        rows = [("A", 1, "b1", 120, 4.0, 4.0, 500.0, 0.0),
                ("B", 1, "b2", 60, 1.0, 0.0, 0.0, 0.75)]
        b = eligible(transform(frame(rows))).row(0, named=True)
        self.assertEqual(b['assessment_kind'], 'before_expiry')
        self.assertAlmostEqual(b['proposed_kl'], 2.0)
        self.assertAlmostEqual(b['source_remaining_before_kl'], 4.0)
        self.assertAlmostEqual(b['source_remaining_after_kl'], 2.0)
        self.assertAlmostEqual(b['network_remaining_before_kl'], 4.0)
        self.assertAlmostEqual(b['network_remaining_after_kl'], 2.0)
        self.assertAlmostEqual(b['net_benefit_kl'], 2.0)

    def test_no_transfer_when_source_can_sell_it_locally(self):
        rows = [("A", 1, "b1", 120, 4.0, 4.0, 500.0, 1.0),
                ("B", 1, "b2", 60, 1.0, 0.0, 0.0, 3.0)]
        self.assertEqual(transform(frame(rows))['rejection_reason'].to_list(), ['source_sells_locally'])

    def test_does_not_displace_destination_stock(self):
        rows = [("A", 1, "b1", 120, 4.0, 4.0, 500.0, 0.0),
                ("B", 1, "b2", 150, 4.0, 0.0, 0.0, 1.0)]
        self.assertEqual(eligible(transform(frame(rows))).height, 0)

    def test_zero_stock_sales_town_is_included(self):
        demand = pl.DataFrame({'town':['A','B'], 'material_code':[1,1], 'rate_kl_month':[0.,0.5]})
        rows = [("A", 1, "b1", 120, 4.0, 4.0, 500.0, 0.0)]
        b = eligible(transform(frame(rows), demand)).row(0, named=True)
        self.assertEqual(b['dest_town'], 'B')
        self.assertAlmostEqual(b['proposed_kl'], 2.)
        self.assertAlmostEqual(b['dest_stock_kl'], 0.)

    def test_unknown_source_rate_does_not_mean_zero_sales(self):
        rows = [("A", 1, "b1", 120, 4.0, 4.0, 500.0, None),
                ("B", 1, "b2", 60, 1.0, 0.0, 0.0, 3.0)]
        b = transform(frame(rows)).row(0, named=True)
        self.assertEqual(b['rejection_reason'], 'unknown_source_rate')
        self.assertIsNone(b['source_remaining_before_kl'])

    def test_all_sources_retained_without_any_other_town(self):
        out = transform(frame([("A", 1, "b1", 120, 4., 4., 500., 0.)]))
        self.assertEqual(out.height, 1)
        self.assertIsNone(out.item(0,'dest_town'))
        self.assertEqual(out.item(0,'rejection_reason'), 'no_other_town')

    def test_shared_child_demand_is_not_repeated(self):
        rows = [("A", 1, "b1", 120, 4., 4., 500., 0.),
                ("B", 1, "b2", 150, 0.5, 0., 0., 2.),
                ("B", 2, "b3", 150, 0.5, 0., 0., 2.)]
        rep=frame(rows).with_columns(pl.lit(10).alias('parent_material_code'))
        self.assertEqual(eligible(transform(rep)).height, 0)
        demand=pl.DataFrame({'town':['A','B','B'], 'material_code':[1,1,2], 'rate_kl_month':[0.,0.5,1.5]})
        out=eligible(transform(rep,demand))
        self.assertAlmostEqual(out.item(0,'proposed_kl'),1.5)

    def test_below_300_shared_sales_and_determinism(self):
        rows = [("A", 1, "b1", 120, 4., 4., 500., 0.),
                ("C", 1, "b3", 120, 4., 4., 400., 0.),
                ("B", 1, "b2", 150, 1., 0., 0., 0.75)]
        a=transform(frame(rows))
        self.assertTrue(a.equals(transform(frame(rows[::-1]))))
        self.assertAlmostEqual(float(eligible(a)['proposed_kl'].sum()),2.)

    def test_no_hidden_harm_to_another_batchs_earlier_checkpoint(self):
        rows = [("A", 1, "b1", 120, 4., 4., 500., 0.),
                ("B", 1, "b2", 330, 1., 0., 0., 1.)]
        # There are 3 KL spare by A's expiry, but A sells ahead of B and
        # would stop B clearing before its own 300-day checkpoint in 30 days.
        out = transform(frame(rows))
        self.assertEqual(eligible(out).height, 0)
        self.assertEqual(out.item(0,'rejection_reason'),'displaces_existing_risk')

    def test_partial_quantity_can_preserve_existing_batch_checkpoint(self):
        rows = [("A", 1, "b1", 120, 4., 4., 500., 0.),
                ("B", 1, "b2", 360, 1., 0., 0., 1.)]
        # By B's checkpoint in 60 days it can sell 2 KL: reserve 1 for B.
        b = eligible(transform(frame(rows))).row(0, named=True)
        self.assertAlmostEqual(b['proposed_kl'], 1., places=7)

    def test_no_room_when_queue_exceeds_demand(self):
        rows = [("A", 1, "b1", 400, 8.0, 5.0, 500.0, 1.0),
                ("B", 1, "b2", 50, 20.0, 0.0, 0.0, 3.0)]
        out = transform(frame(rows))
        self.assertEqual(out["rejection_reason"].to_list(), ["no_absorb_capacity"])

    def test_deterministic_under_row_order(self):
        a = transform(frame(self.rows))
        b = transform(frame(self.rows[::-1]))
        self.assertTrue(a.equals(b))

    def test_projection_matches_existing_daily_depletion(self):
        rng = random.Random(47)
        for _ in range(60):
            pool = [(str(i), rng.randint(-10, 160), rng.uniform(.01, 5.)) for i in range(6)]
            monthly, horizon = rng.uniform(0, 5), rng.randint(1, 160)
            daily = deplete([(k,r,q,date(2020,1,1)) for k,r,q in pool], monthly/30, horizon)
            actual = remaining(pool, monthly, horizon)
            for k in actual:
                self.assertAlmostEqual(actual[k],daily[k][-1],places=8)

    def test_ui_payload_preserves_comparison_and_rejected_source(self):
        rep = frame([('A',1,'x',120,4.,4.,500.,0.),('B',1,'y',60,1.,0.,0.,.75),
                     ('C',2,'z',100,1.,1.,10.,0.)]).with_columns(pl.lit(46000).alias('report_date'))
        p=build_payload(rep)
        rows=[dict(zip(p['scols'],r,strict=True)) for r in p['src']]
        a=next(r for r in rows if r['town']=='A')
        self.assertEqual(a['assessment'],'before_expiry')
        self.assertAlmostEqual(a['before'],4.)
        self.assertAlmostEqual(a['after'],2.)
        c=next(r for r in rows if r['town']=='C')
        self.assertEqual(c['checked'],0)
        self.assertEqual(c['rej'],{'no_other_town':1})

    def test_no_exposed_sources_is_a_valid_empty_payload(self):
        rep=frame([('A',1,'x',120,1.,0.,0.,1.)]).with_columns(pl.lit(46000).alias('report_date'))
        self.assertEqual(build_payload(rep)['src'],[])

    def test_invalid_or_duplicate_demand_is_not_silently_consumed(self):
        demand=pl.DataFrame({'town':['A','A'],'material_code':[1,1],'rate_kl_month':[1.,2.]})
        with self.assertRaises(ValueError):
            transform(frame(self.rows),demand)
        with self.assertRaises(ValueError):
            transform(frame(self.rows),demand.head(1).with_columns(pl.lit(-1.).alias('rate_kl_month')))

    def test_same_town_and_expired_excluded(self):
        rows = [("A", 1, "b1", -5, 8.0, 5.0, 500.0, 1.0),
                ("A", 1, "b2", 90, 1.0, 0.0, 0.0, 1.0),
                ("B", 1, "b3", 90, 1.0, 0.0, 0.0, 1.0)]
        out = transform(frame(rows))
        self.assertEqual(out["dest_town"].to_list(), ["B"])
        self.assertEqual(out["rejection_reason"].to_list(), ["expired_source"])


if __name__ == "__main__":
    unittest.main()
