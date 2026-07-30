import './SegmentedDisplay.css';

/**
 * Amber-on-dark inset well readout used everywhere a number/countdown/time
 * needs to render like a real 7/14-segment LED, per Section 1.1/1.5 of the
 * design spec. Renders a dim "ghost" of all-segments-lit behind the real
 * value so unlit segments stay faintly visible — the detail that makes a
 * segmented display read as real hardware instead of a styled <span>.
 *
 * @param {{ value: string, size?: 'sm'|'md'|'lg', tone?: 'amber'|'signal'|'safe' }} props
 */
// DSEG has no glyph for '%' (real segmented hardware never lights one either —
// it's silkscreened on the bezel next to the digits). Split it off and render
// it in the UI font instead of letting the browser silently fall back fonts
// mid-string, which looks broken next to blocky segment digits.
const SUFFIX_RE = /(%+)$/;

export default function SegmentedDisplay({ value, size = 'md', tone = 'amber' }) {
  const str    = String(value);
  const match  = str.match(SUFFIX_RE);
  const main   = match ? str.slice(0, match.index) : str;
  const suffix = match ? match[1] : '';
  const seg14  = /[A-Za-z]/.test(main);
  const ghost  = main.replace(/[0-9A-Za-z]/g, '8');

  return (
    <span className={`seg-well seg-well--${size}`}>
      <span className={`seg ${seg14 ? 'seg--seg14' : 'seg--seg7'} seg--${tone}`}>
        <span className="seg__ghost" aria-hidden="true">{ghost}</span>
        <span className="seg__value">{main}</span>
      </span>
      {suffix && <span className={`seg__suffix seg__suffix--${tone}`}>{suffix}</span>}
    </span>
  );
}
