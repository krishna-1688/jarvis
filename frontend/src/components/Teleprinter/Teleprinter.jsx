import { useEffect, useState } from 'react';
import './Teleprinter.css';

/**
 * Live transcription readout, typed letter-by-letter (Section 1.2).
 * @param {{ text: string, speed?: number }} props  speed is ms per character.
 */
export default function Teleprinter({ text, speed = 8 }) {
  const [shown, setShown] = useState('');

  useEffect(() => {
    setShown('');
    if (!text) return undefined;

    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) clearInterval(id);
    }, speed);
    return () => clearInterval(id);
  }, [text, speed]);

  return (
    <div className="teleprinter mono">
      <span className="teleprinter__text">{shown}</span>
      <span className="teleprinter__cursor" aria-hidden="true">▌</span>
    </div>
  );
}
