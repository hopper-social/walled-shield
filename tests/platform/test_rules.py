import json
import shutil
import subprocess

import pytest

from walled_guard import rules
from walled_guard.rules import BUILTINS, Rule, RuleEngine, default_rules


def engine(*extra, defaults=True):
    base = default_rules() if defaults else []
    for r in base:
        r.enabled = True
    return RuleEngine(base + list(extra))


@pytest.mark.parametrize("key", list(BUILTINS))
def test_every_detector_matches_its_own_example(key):
    det = BUILTINS[key]
    hits = RuleEngine([Rule(det.label, "builtin", key, id=key)]).scan(f"see {det.example} here")
    assert [h.rule_id for h in hits] == [key]


def test_validators_cut_false_positives():
    assert rules.luhn("4111 1111 1111 1111") and not rules.luhn("4111 1111 1111 1112")
    assert not rules.luhn("0000 0000 0000 0000")
    assert rules.verhoeff(BUILTINS["aadhaar"].example) and not rules.verhoeff("2345 6789 0123")
    e = engine()
    assert not e.scan("order 4111 1111 1111 1112 shipped")           # fails Luhn
    assert not e.scan("call 1234 5678 9012 now")                       # Aadhaar can't start with 1


def test_masking_never_reveals_full_value():
    hits = engine().scan("card 4111-1111-1111-1111")
    assert hits[0].sample == "•••• 1111"
    key = "ghp_" + "a1B2" * 9
    sample = engine().scan(key)[0].sample
    assert key not in sample and sample.startswith("ghp")


def test_keyword_modes():
    e = RuleEngine([Rule("Acme", "keyword", "Acme Corp")])
    assert e.scan("contract with acme corp")                          # case-insensitive by default
    assert not e.scan("Acme\nCorp", "typed")
    assert e.scan("Acme\nCorp", "extracted")                          # PDFs split words
    marker = RuleEngine([Rule("m", "keyword", "WALLED_SENSITIVE", case_sensitive=True)])
    assert marker.scan("WA11ED SENS1TIVE", "ocr") and not marker.scan("WA11ED SENS1TIVE", "typed")


def test_actions_and_phone_policy():
    hits = engine(Rule("w", "keyword", "alpha", action="warn", phone="off"),
                  Rule("b", "keyword", "beta", action="block", phone="approve")).scan("alpha beta")
    assert rules.strongest(hits) == "block" and rules.phone_mode(hits) == "approve"
    assert rules.strongest([]) == "warn"


def test_surfaces():
    only_web = Rule("x", "keyword", "secretword", mcp=False)
    e = RuleEngine([only_web])
    assert e.scan("secretword", surface="browser") and not e.scan("secretword", surface="mcp")


def test_filename_and_folder_rules(tmp_path):
    e = RuleEngine([Rule("payroll", "filename", "*payroll*"), Rule("fin", "folder", str(tmp_path / "Finance"))])
    assert e.scan_filename("March_PAYROLL.xlsx")
    assert e.scan_paths([str(tmp_path / "Finance" / "q1.pdf")])
    assert not e.scan_paths([str(tmp_path / "Finances" / "q1.pdf")])         # sibling folder
    assert e.scan_paths(["/x/y/payroll-2026.csv"])


@pytest.mark.parametrize("pattern", [r"(?i)abc", r"(?P<x>a)", r"a++", r"\Aabc"])
def test_python_only_regex_is_rejected(pattern):
    with pytest.raises(ValueError):
        Rule("bad", "regex", pattern).validate()


def test_invalid_rules_rejected():
    for bad in (Rule("", "keyword", "x"), Rule("x", "nope", "x"), Rule("x", "builtin", "unknown"),
                Rule("x", "keyword", "y", action="explode"), Rule("x", "regex", "(")):
        with pytest.raises(ValueError):
            bad.validate()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_browser_export_compiles_and_agrees_in_javascript():
    """The extension runs the same patterns; make sure JS compiles them and finds the same hits."""
    e = engine(Rule("emp", "regex", r"\bEMP-\d{6}\b"))
    samples = [d.example for d in BUILTINS.values()] + ["EMP-123456", "WALLED_SENSITIVE", "nothing here 1234"]
    expected = [[h.rule_id for h in e.scan(s)] for s in samples]
    script = r"""
    const [rules, samples] = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    function luhn(v){const d=v.replace(/\D/g,'').split('').map(Number);if(d.length<13||d.length>19||new Set(d).size===1)return false;let s=0;d.reverse().forEach((x,i)=>{if(i%2){x*=2;if(x>9)x-=9}s+=x});return s%10===0}
    const VD=[[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]];
    const VP=[[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]];
    function verhoeff(v){const d=v.replace(/\D/g,'').split('').map(Number);if(d.length!==12)return false;let c=0;d.reverse().forEach((x,i)=>{c=VD[c][VP[i%8][x]]});return c===0}
    const V={luhn,verhoeff};
    const out = samples.map(s => rules.filter(r => {
      if (r.kind === 'keyword') return r.case ? s.includes(r.pattern) : s.toLowerCase().includes(r.pattern.toLowerCase());
      const re = new RegExp(r.pattern, (r.flags||'') + 'g');
      for (const m of s.matchAll(re)) if (!r.validator || V[r.validator](m[0])) return true;
      return false;
    }).map(r => r.id));
    console.log(JSON.stringify(out));
    """
    proc = subprocess.run(["node", "-e", script], input=json.dumps([e.browser_export(), samples]),
                          capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout) == expected
