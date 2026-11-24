"""The CIMD resolver's cache: tiers, known clients, lifetimes and backoff.

Attacks covered: attacker documents flooding the LRU, sign-ins with one's own
client ids buying protection, a document stretching its own lifetime, a known
client's host failing, and one id being refetched as fast as it is asked for.
Fakes are in ``tests/cimd_fakes.py``; the fetch machinery is
``tests/test_oauth_cimd_resolver.py``.
"""

from unittest.mock import patch

import anyio
import pytest

from api_server.oauth import cimd, cimd_cache
from api_server.oauth.cimd_document import ClientMetadataError, TransientFetchError
from services._ssrf_guard import validate_and_pin_async
from tests.cimd_fakes import KNOWN, USER, CimdCase, cid, settled, small_resolver
from tests.oauth_server_fixture import (
    CLIENT_ID,
    USER_ID,
    OAuthServerCase,
    client_document,
)
from tests.test_template import TestTemplate


class TestCacheTiers(CimdCase):
    @pytest.mark.anyio
    async def test_unknown_ids_cannot_evict_a_known_client(self):
        """F4: the LRU is attacker-fillable; the protected tier is not."""
        with patch.object(cimd, "_resolver", small_resolver(cache_entries=3)):
            await cimd.resolve_client(KNOWN)
            cimd.mark_known(KNOWN, USER)  # moves the record out of the LRU
            for n in range(10):
                await cimd.resolve_client(cid(n))
            state = cimd.snapshot()
            assert len(state.cached) == 3
            assert KNOWN in state.protected
            await cimd.resolve_client(KNOWN)
        assert self.net.calls[KNOWN] == 1  # served from the protected tier

    @pytest.mark.anyio
    async def test_one_user_vouches_for_a_bounded_number_of_ids(self):
        """N1(c): sign-ins with one's own client ids buy at most three at once."""
        for day, n in enumerate(range(5)):
            self.clock.now += day * cimd_cache.VOUCH_WINDOW_SECONDS
            await cimd.resolve_client(cid(n))
            cimd.mark_known(cid(n), "user_attacker")
        state = cimd.snapshot()
        assert state.known == (cid(2), cid(3), cid(4))
        # The ids it dropped keep their records, as ordinary cache entries.
        assert {cid(0), cid(1)} <= set(state.cached)
        cimd.mark_known(cid(0), USER)  # another user still vouches for it
        assert cid(0) in cimd.snapshot().known

    @pytest.mark.anyio
    async def test_new_vouches_are_a_rate_and_revouching_is_free(self):
        """H1(2): token use re-vouches on every request, so the cap is a rate:
        rotating ids back in costs a new vouch each, three per day."""
        for n in range(20):
            cimd.mark_known(cid(n), "user_attacker")
            for m in range(n + 1):  # every token use re-vouches
                cimd.mark_known(cid(m), "user_attacker")
        assert cimd.snapshot().known == (cid(0), cid(1), cid(2))
        self.clock.now += cimd_cache.VOUCH_WINDOW_SECONDS + 1
        cimd.mark_known(cid(3), "user_attacker")
        assert cid(3) in cimd.snapshot().known

    @pytest.mark.anyio
    async def test_vouchers_per_id_are_capped(self):
        """L3: a popular id keeps a bounded voucher set, and stays known."""
        for n in range(cimd_cache.MAX_VOUCHERS_PER_ID + 10):
            cimd.mark_known(KNOWN, f"user_{n}")
        resolver = cimd._resolver
        assert len(resolver.known[KNOWN]) == cimd_cache.MAX_VOUCHERS_PER_ID
        assert len(resolver.subjects) == cimd_cache.MAX_VOUCHERS_PER_ID

    @pytest.mark.anyio
    async def test_scheme_and_host_case_share_one_entry(self):
        await cimd.resolve_client(KNOWN)
        shouty = KNOWN.replace(
            "https://known.test.example", "HTTPS://Known.Test.Example"
        )
        assert (await cimd.resolve_client(shouty)).client_id == KNOWN
        cimd.mark_known(shouty, USER)
        assert cimd.snapshot().known == (KNOWN,)
        assert self.net.calls[KNOWN] == 1

    @pytest.mark.anyio
    async def test_an_id_stays_known_while_any_voucher_remains(self):
        cimd.mark_known(KNOWN, USER)
        cimd.mark_known(KNOWN, "user_other")
        for n in range(3):
            cimd.mark_known(cid(n), USER)  # USER moves on to three others
        assert KNOWN in cimd.snapshot().known

    @pytest.mark.anyio
    async def test_evicted_known_record_moves_to_the_cache(self):
        """N2: flushing the known set demotes records instead of dropping them."""
        with patch.object(cimd, "_resolver", small_resolver(known_clients=2)):
            cimd.mark_known(KNOWN, USER)
            await cimd.resolve_client(KNOWN)
            cimd.mark_known(cid(1), "u1")
            cimd.mark_known(cid(2), "u2")
            state = cimd.snapshot()
            assert KNOWN not in state.known
            assert KNOWN in state.cached
            await cimd.resolve_client(KNOWN)
        assert self.net.calls[KNOWN] == 1

    @pytest.mark.anyio
    async def test_records_past_every_use_are_dropped_on_read(self):
        """N3: no record outlives its bound: the stale bound for unknown ids,
        KNOWN_MAX_AGE_SECONDS for known ones."""
        cimd.mark_known(KNOWN, USER)
        await cimd.resolve_client(KNOWN)
        await cimd.resolve_client(cid(1))
        self.clock.now += (
            cimd_cache.DEFAULT_FRESH_SECONDS + cimd_cache.MAX_STALE_SECONDS + 1
        )
        self.net.answer[cid(1)] = TransientFetchError("HTTP 503")
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(cid(1))
        assert cid(1) not in cimd.snapshot().cached
        assert KNOWN in cimd.snapshot().protected
        self.clock.now += cimd_cache.KNOWN_MAX_AGE_SECONDS
        self.net.answer[KNOWN] = TransientFetchError("HTTP 503")
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(KNOWN)
        assert KNOWN not in cimd.snapshot().protected


class TestKnownByIssuance(OAuthServerCase):
    def test_a_completed_sign_in_protects_the_client(self):
        assert CLIENT_ID not in cimd.snapshot().known
        code, verifier = self._sign_in()
        assert CLIENT_ID in cimd.snapshot().known  # a code was issued
        assert CLIENT_ID in cimd.snapshot().protected
        cimd.clear_cache()
        with patch.object(cimd, "mark_known", wraps=cimd.mark_known) as marked:
            assert self._exchange(code, verifier)["status"] == 200
        marked.assert_called_with(CLIENT_ID, USER_ID)  # so were tokens


class TestFreshness(CimdCase):
    @pytest.mark.anyio
    async def test_max_age_is_honoured(self):
        self.net.answer[cid(1)] = (client_document(client_id=cid(1)), "max-age=120")
        await cimd.resolve_client(cid(1))
        self.clock.now += 119
        await cimd.resolve_client(cid(1))
        assert self.net.calls[cid(1)] == 1
        self.clock.now += 2
        await cimd.resolve_client(cid(1))
        assert self.net.calls[cid(1)] == 2

    @pytest.mark.anyio
    async def test_max_age_is_clamped(self):
        self.net.answer[cid(1)] = (client_document(client_id=cid(1)), "max-age=0")
        self.net.answer[cid(2)] = (client_document(client_id=cid(2)), "max-age=9e9")
        self.net.answer[cid(3)] = (
            client_document(client_id=cid(3)),
            "max-age=99999999",
        )
        for n in (1, 2, 3):
            await cimd.resolve_client(cid(n))
        self.clock.now += cimd_cache.MIN_FRESH_SECONDS - 1
        await cimd.resolve_client(cid(1))
        assert self.net.calls[cid(1)] == 1  # max-age=0 still gets the floor
        self.clock.now += cimd_cache.MAX_FRESH_SECONDS
        for n in (1, 2, 3):
            await cimd.resolve_client(cid(n))
        assert [self.net.calls[cid(n)] for n in (1, 2, 3)] == [2, 2, 2]

    @pytest.mark.anyio
    async def test_no_store_is_never_served_stale(self):
        """F5: a document that forbids storing is not kept past the floor."""
        self.net.answer[cid(1)] = (client_document(client_id=cid(1)), "no-store")
        await cimd.resolve_client(cid(1))
        self.clock.now += cimd_cache.MIN_FRESH_SECONDS + 1
        self.net.answer[cid(1)] = TransientFetchError("HTTP 503")
        with pytest.raises(ClientMetadataError, match="503"):
            await cimd.resolve_client(cid(1))

    @pytest.mark.anyio
    async def test_staleness_is_bounded(self):
        """F5: past freshness plus _MAX_STALE_SECONDS nothing is served."""
        await cimd.resolve_client(cid(1))
        self.net.answer[cid(1)] = TransientFetchError("HTTP 503")
        self.clock.now += (
            cimd_cache.DEFAULT_FRESH_SECONDS + cimd_cache.MAX_STALE_SECONDS - 1
        )
        assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        self.clock.now += cimd_cache.BACKOFF_SECONDS
        with pytest.raises(ClientMetadataError, match="503"):
            await cimd.resolve_client(cid(1))

    @pytest.mark.anyio
    async def test_busy_serves_stale_only_within_the_bound(self):
        await cimd.resolve_client(cid(1))
        self.clock.now += cimd_cache.DEFAULT_FRESH_SECONDS + 1
        cimd._resolver.budget.running = cimd._resolver.budget.limit  # all taken
        assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        self.clock.now += cimd_cache.MAX_STALE_SECONDS
        with pytest.raises(ClientMetadataError, match="too many"):
            await cimd.resolve_client(cid(1))
        cimd._resolver.budget.running = 0


class TestBackoff(CimdCase):
    @pytest.mark.anyio
    async def test_a_failing_id_is_not_refetched_until_its_backoff_ends(self):
        """N1(b): one transient failure buys a quiet period, not a retry storm."""
        self.net.answer[cid(1)] = TransientFetchError("HTTP 503")
        for _ in range(5):
            with pytest.raises(ClientMetadataError, match="503"):
                await cimd.resolve_client(cid(1))
        assert self.net.calls[cid(1)] == 1
        assert cid(1) not in cimd.snapshot().failed  # not a definite failure
        self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
        del self.net.answer[cid(1)]
        assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        assert self.net.calls[cid(1)] == 2

    @pytest.mark.anyio
    async def test_backoff_serves_stale_without_fetching(self):
        await cimd.resolve_client(cid(1))
        self.clock.now += cimd_cache.DEFAULT_FRESH_SECONDS + 1
        self.net.answer[cid(1)] = TransientFetchError("HTTP 502")
        for _ in range(3):
            assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        assert self.net.calls[cid(1)] == 2
        assert cid(1) in cimd.snapshot().backing_off


class TestFullBudget(CimdCase):
    @pytest.mark.anyio
    async def test_a_full_budget_leaves_no_state(self):
        """M1: poking an id while a flood fills the budget must not make it
        fail after the flood ends."""
        cimd._resolver.budget.running = cimd._resolver.budget.limit
        with pytest.raises(ClientMetadataError, match="too many"):
            await cimd.resolve_client(cid(1))
        cimd._resolver.budget.running = 0
        # Resolver threads all taken: the fetch runs, but DNS is refused.
        threads = cimd._resolver.budget.resolver
        with (
            patch.object(threads, "_limit", 0),
            patch.object(cimd, "validate_and_pin_async", validate_and_pin_async),
            pytest.raises(ClientMetadataError, match="too many"),
        ):
            await cimd.resolve_client(cid(2))
        state = cimd.snapshot()
        assert state.backing_off == () and state.failed == ()
        assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        assert (await cimd.resolve_client(cid(2))).client_id == cid(2)


class TestKnownClientErrors(CimdCase):
    async def _stale_known(self) -> None:
        cimd.mark_known(KNOWN, USER)
        await cimd.resolve_client(KNOWN)
        self.clock.now += cimd_cache.DEFAULT_FRESH_SECONDS + 1

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "error", [TransientFetchError("HTTP 503"), TransientFetchError("network")]
    )
    async def test_transient_error_from_a_known_host_serves_stale(self, error):
        """F6 + N1(b): a 5xx or network error keeps the known client signing
        in, and its host is asked again only after the backoff."""
        await self._stale_known()
        self.net.answer[KNOWN] = error
        for _ in range(3):
            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            await settled(KNOWN)
        assert KNOWN not in cimd.snapshot().failed
        assert self.net.calls[KNOWN] == 2  # one background refresh, then quiet
        self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
        await settled(KNOWN)
        assert self.net.calls[KNOWN] == 3

    @pytest.mark.anyio
    async def test_known_record_outlives_the_stale_bound_until_an_answer(self):
        """H1(1): refreshes that keep failing without an answer, or finding
        the budget full, never take a known client offline before an answer."""
        await self._stale_known()
        self.net.answer[KNOWN] = TransientFetchError("HTTP 503")
        for _ in range(3):
            self.clock.now += cimd_cache.MAX_STALE_SECONDS  # well past the bound
            assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
            await settled(KNOWN)
        cimd._resolver.known_budget.running = cimd._resolver.known_budget.limit
        self.clock.now += cimd_cache.MAX_STALE_SECONDS
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
        cimd._resolver.known_budget.running = 0
        self.net.answer[KNOWN] = ClientMetadataError("HTTP 404")
        self.clock.now += cimd_cache.BACKOFF_SECONDS + 1
        await cimd.resolve_client(KNOWN)  # stale, while the 404 comes back
        await settled(KNOWN)
        with pytest.raises(ClientMetadataError, match="404"):
            await cimd.resolve_client(KNOWN)

    @pytest.mark.anyio
    async def test_a_no_store_known_record_is_not_kept(self):
        self.net.answer[KNOWN] = (client_document(client_id=KNOWN), "no-store")
        await self._stale_known()
        self.net.answer[KNOWN] = TransientFetchError("HTTP 503")
        with pytest.raises(ClientMetadataError, match="503"):
            await cimd.resolve_client(KNOWN)

    @pytest.mark.anyio
    async def test_timeout_from_a_known_host_serves_stale(self):
        await self._stale_known()
        self.net.hold[KNOWN] = anyio.Event()  # never answers
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN
        await settled(KNOWN)
        assert KNOWN in cimd.snapshot().protected
        assert (await cimd.resolve_client(KNOWN)).client_id == KNOWN

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "answer",
        [
            ClientMetadataError("client metadata fetch returned HTTP 404"),
            b"not json",
            client_document(client_id=cid("other")),
        ],
    )
    async def test_definite_failure_drops_the_record(self, answer):
        """F6: a 4xx, invalid or mismatched document is an answer: no stale."""
        await self._stale_known()
        self.net.answer[KNOWN] = answer
        await cimd.resolve_client(KNOWN)  # stale, while the refresh runs
        await settled(KNOWN)
        assert KNOWN not in cimd.snapshot().protected
        with pytest.raises(ClientMetadataError):
            await cimd.resolve_client(KNOWN)

    @pytest.mark.anyio
    async def test_unknown_client_gets_stale_on_transient_error_only(self):
        await cimd.resolve_client(cid(1))
        await cimd.resolve_client(cid(2))
        self.clock.now += cimd_cache.DEFAULT_FRESH_SECONDS + 1
        self.net.answer[cid(1)] = TransientFetchError("HTTP 502")
        self.net.answer[cid(2)] = ClientMetadataError("HTTP 404")
        assert (await cimd.resolve_client(cid(1))).client_id == cid(1)
        with pytest.raises(ClientMetadataError, match="404"):
            await cimd.resolve_client(cid(2))


class TestCacheControl(TestTemplate):
    def test_cache_control_parsing(self):
        fresh, stale = cimd_cache.lifetimes(None)
        assert (fresh, stale) == (
            cimd_cache.DEFAULT_FRESH_SECONDS,
            cimd_cache.MAX_STALE_SECONDS,
        )
        assert cimd_cache.lifetimes("public, max-age=600")[0] == 600
        assert cimd_cache.lifetimes('max-age="600"')[0] == 600
        assert cimd_cache.lifetimes("max-age=-5")[0] == cimd_cache.DEFAULT_FRESH_SECONDS
        assert cimd_cache.lifetimes("No-Store") == (cimd_cache.MIN_FRESH_SECONDS, 0)
        assert cimd_cache.lifetimes("max-age=600, no-cache")[1] == 0
