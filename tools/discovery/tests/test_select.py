from discovery.pipeline import select

INVENTORY = [
    "tests/a_test.py::test_x",
    "tests/sub/b_test.py::TestCls::test_y",
]


def test_a_file_selects_every_test_beneath_it():
    assert select(INVENTORY, ["tests/a_test.py"]) == ["tests/a_test.py::test_x"]


def test_a_directory_selects_recursively():
    assert select(INVENTORY, ["tests"]) == INVENTORY
    assert select(INVENTORY, ["tests/sub"]) == [INVENTORY[1]]


def test_an_exact_node_id_selects_itself():
    assert select(INVENTORY, [INVENTORY[1]]) == [INVENTORY[1]]


def test_an_unmatched_selector_selects_nothing():
    assert select(INVENTORY, ["does/not/exist"]) == []


def test_a_selector_matching_several_entries_does_not_duplicate_them():
    assert select(INVENTORY, ["tests", "tests/a_test.py"]) == INVENTORY
