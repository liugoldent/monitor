import unittest
from datetime import datetime
from compare_exit_rules import target_books, replay, START, END


class ExitComparisonTests(unittest.TestCase):
    def test_opposite_entry_offsets_without_leaving_a_hidden_short(self):
        books = [('A',1,100)]
        offset = target_books(books, (START,1,'B',0,-1),'offset')
        self.assertEqual(offset, [])
        self.assertEqual(target_books(offset,(START,2,'B',-1,0),'offset'), [])
        own = target_books(books,(START,1,'B',0,-1),'own_strict')
        self.assertEqual(own,[('A',1,100),('B',-1,None)])
        self.assertEqual(target_books(own,(START,2,'B',-1,0),'own_strict'),books)

    def test_new_modes_accept_only_explicit_entries_and_close_one(self):
        books=[('A',1,100),('B',1,110)]
        self.assertEqual(target_books(books,(START,1,'C',0,-1),'offset'),books[1:])
        self.assertEqual(target_books(books,(START,1,'C',1,0),'offset'),books[1:])
        self.assertEqual(target_books(books,(START,1,'C',1,0),'own_strict'),books)
        for mode in ('offset','own_strict'):
            self.assertEqual(target_books(books,(START,1,'A',1,-1),mode),books)
            self.assertEqual(target_books(books,(START,1,'flat',0,0),mode),[])
        short=[('A',-1,100)]
        self.assertEqual(target_books(short,(START,1,'B',0,1),'offset'),[])

    def test_unowned_exit_only_closes_fifo_same_direction_in_any_mode(self):
        books = [('A', 1, 100), ('B', -1, 105), ('C', 1, 110)]
        event = (START, 0, 'D', 1, 0)
        self.assertEqual(target_books(books, event, 'own'), books)
        self.assertEqual(target_books(books, event, 'any'), books[1:])
        self.assertEqual(target_books([('B', -1, 105)], event, 'any'), [('B', -1, 105)])

    def test_flat_clears_both_directions_and_reverse_is_ignored(self):
        books = [('A', 1, 100), ('B', 1, 105)]
        for mode in ('own', 'any'):
            self.assertEqual(target_books(books, (START, -1, 'flat', 0, 0), mode), [])
        reverse = target_books(books, (START, 1, 'C', 1, -1), 'any')
        self.assertEqual(reverse, books)
        self.assertEqual(target_books(books, (START, 1, 'C', -1, 1), 'any'), books)

    def test_cap_rejection_does_not_create_owned_position(self):
        events = [(START, i, code, 0, 1) for i, code in enumerate(('A','B','C'))]
        events += [(START, 3, 'C', 1, 0)]
        bars = {datetime(2026,6,25,8,46): {'open':100,'close':100},
                datetime(2026,9,17,12,18): {'open':100,'close':100}}
        own = replay(events, bars, 'own')
        any_exit = replay(events, bars, 'any')
        self.assertEqual((own['ending_position'],own['rejected'],own['turnover']), (2,1,2))
        self.assertEqual((any_exit['ending_position'],any_exit['rejected'],any_exit['turnover']), (1,1,3))


if __name__ == '__main__':
    unittest.main()
