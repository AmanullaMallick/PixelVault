import itertools
import os
import unittest

from core import ecc_matrix as ecc


class TestErasure(unittest.TestCase):
    def _shards(self, k, length=257):
        return [os.urandom(length) for _ in range(k)]

    def test_any_k_of_n_recovers_all_combinations(self):
        for k, m in [(3, 2), (4, 2), (2, 1)]:
            data = self._shards(k)
            parity = ecc.encode(data, m)
            everything = data + parity
            for keep in itertools.combinations(range(k + m), k):
                survivors = {i: everything[i] for i in keep}
                rebuilt = ecc.decode(survivors, k, m)
                self.assertEqual(rebuilt, data, f"k={k} m={m} keep={keep}")

    def test_no_parity_requires_all_shards(self):
        data = self._shards(3)
        self.assertEqual(ecc.encode(data, 0), [])
        with self.assertRaises(ValueError):
            ecc.decode({0: data[0], 1: data[1]}, 3, 0)

    def test_too_few_shards_raises(self):
        data = self._shards(4)
        parity = ecc.encode(data, 2)
        with self.assertRaises(ValueError):
            ecc.decode({0: data[0], 4: parity[0]}, 4, 2)

    def test_unequal_lengths_rejected(self):
        with self.assertRaises(ValueError):
            ecc.encode([b"aa", b"b"], 1)


if __name__ == "__main__":
    unittest.main()
