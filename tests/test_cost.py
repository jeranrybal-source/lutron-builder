"""What a read costs, and why it stopped costing $15.

The burn (found on a real House A read, 2026-08-07): web search is a
SERVER-side tool, so every fixture lookup is another sampling pass over the
whole context -- and without cache markers every pass re-billed the entire
drawing set at full input price. Sixty lookups at ~32k tokens each is ~$10 of
input before a single schedule row is written.
"""
import inspect

from hwwriter import ingest
from hwwriter.ingest import cost_breakdown, cost_report_lines, mark_cacheable


def test_the_breakdown_prices_each_stage_correctly():
    # Opus 5: $5/M in, $25/M out; cache write 1.25x, cache read 0.1x; 1c/search.
    b = cost_breakdown("claude-opus-5", fresh_in=100_000, out=20_000,
                       cache_write=200_000, cache_read=400_000, searches=12)
    assert b["reading_usd"] == 1.75      # (100k + 1.25 * 200k) * $5/M
    assert b["research_rereads_usd"] == 0.20   # 400k * $5/M * 0.1
    assert b["research_usd"] == 0.32     # rereads + 12 * $0.01
    assert b["writing_usd"] == 0.50      # 20k * $25/M
    assert b["total_usd"] == 2.57


def test_an_uncached_read_prices_the_old_way():
    b = cost_breakdown("claude-opus-5", fresh_in=2_000_000, out=60_000)
    assert b["reading_usd"] == 10.0
    assert b["research_usd"] == 0.0
    assert b["total_usd"] == 11.5


def test_the_lines_read_as_plain_english():
    b = cost_breakdown("claude-opus-5", fresh_in=100_000, out=20_000,
                       cache_write=200_000, cache_read=400_000, searches=12)
    lines = cost_report_lines(b)
    text = "\n".join(lines)
    assert "What this read cost: ~$2.57" in text
    assert "reading the drawings" in text and "cached for reuse" in text
    assert "12 searches" in text
    assert "writing the schedules" in text


def test_no_research_means_no_research_line():
    lines = cost_report_lines(cost_breakdown("claude-opus-5", 50_000, 10_000))
    assert not any("research" in x for x in lines)


def test_cache_marker_lands_on_the_last_block_only():
    blocks = [{"type": "document", "source": {}},
              {"type": "document", "source": {}}]
    marked = mark_cacheable(blocks)
    assert "cache_control" not in marked[0]
    assert marked[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1], "the caller's list was mutated"
    assert mark_cacheable([]) == []


def test_the_request_actually_carries_the_cache_markers():
    # The function existing is not the fix; the call site is. Same shape as
    # the keypad-family bug: right function, wrong (or missing) call.
    src = inspect.getsource(ingest.run)
    assert "mark_cacheable(blocks)" in src, (
        "the PDF blocks are sent uncacheable -- every research pass re-bills "
        "the whole drawing set at full price")
    assert '"cache_control": {"type": "ephemeral"}' in src, (
        "the system prompt is sent uncacheable")


def test_search_count_comes_from_the_api_not_the_stream():
    # Some SDK versions put content_block on the STOP event too; counting both
    # showed "60 fixture lookups" for 30. The final figure must come from the
    # API's own usage, and the live ticker must count starts only.
    src = inspect.getsource(ingest.run)
    assert "web_search_requests" in src
    assert 'etype == "content_block_start"' in src


def test_the_offered_model_is_priced_against_the_real_work():
    from hwwriter.ingest import cost_breakdown, model_estimates
    profile = {"fresh_in": 40_000, "cache_write": 40_000,
               "cache_read": 800_000, "out": 50_000, "searches": 25}
    est = {e["id"]: e["usd"] for e in model_estimates(profile)}
    assert set(est) == {c["id"] for c in ingest.MODEL_CHOICES}
    # The estimate must be the same arithmetic the post-read breakdown uses,
    # or the app quotes one number and bills another.
    assert est["claude-opus-5"] == cost_breakdown(
        "claude-opus-5", 40_000, 50_000, 40_000, 800_000, 25)["total_usd"]


def test_withdrawn_models_can_still_be_priced_but_are_not_offered():
    """A project READ on a withdrawn model must still show its cost.

    Sonnet and Fable were removed from the app on 2026-08-07 after testing
    (see MODEL_NOTE). Dropping them from PRICING too would make an older
    project's cost readout silently read $0.
    """
    offered = {c["id"] for c in ingest.MODEL_CHOICES}
    assert offered == {"claude-opus-5"}
    for withdrawn in ("claude-sonnet-5", "claude-fable-5"):
        assert withdrawn in ingest.PRICING, f"{withdrawn} can no longer be costed"
        assert withdrawn not in offered


def test_turning_research_off_removes_the_rereads_and_the_searches():
    from hwwriter.ingest import model_estimates, without_research
    profile = {"fresh_in": 40_000, "cache_write": 40_000,
               "cache_read": 800_000, "out": 50_000, "searches": 25}
    assert without_research(profile)["cache_read"] == 0
    assert without_research(profile)["searches"] == 0
    assert without_research(profile)["fresh_in"] == 40_000, "the sheets are still read"
    on = {e["id"]: e["usd"] for e in model_estimates(profile, research=True)}
    off = {e["id"]: e["usd"] for e in model_estimates(profile, research=False)}
    for mid in on:
        assert off[mid] < on[mid], f"{mid}: research-off is not cheaper"


def test_the_report_footer_travels_with_the_read():
    src = inspect.getsource(ingest.run)
    assert "cost_report_lines" in src
    assert "EXTRACTION-REPORT.txt" in src
