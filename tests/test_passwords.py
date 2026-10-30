from salesonboard.passwords import hash_password, verify_password


def test_right_password_matches():
    stored = hash_password("demo-company1")
    assert verify_password("demo-company1", stored)


def test_wrong_password_does_not_match():
    stored = hash_password("demo-company1")
    assert not verify_password("demo-company2", stored)


def test_hash_never_contains_the_password():
    assert "demo-company1" not in hash_password("demo-company1")


def test_same_password_gives_different_hashes():
    # Each hash gets its own random salt.
    assert hash_password("same") != hash_password("same")


def test_broken_hash_never_matches():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "scrypt$zz$zz")
