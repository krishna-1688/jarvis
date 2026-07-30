import './ModuleTray.css';

/** Slides up from the rack's bottom edge in edit mode (Section 1.4). */
export default function ModuleTray({ visible, available, onAdd, onReset }) {
  return (
    <div className={`module-tray ${visible ? 'is-visible' : ''}`}>
      <div className="module-tray__inner">
        <span className="module-tray__label">
          {available.length > 0 ? 'AVAILABLE MODULES' : 'ALL MODULES ACTIVE'}
        </span>
        <div className="module-tray__items">
          {available.map((def) => (
            <button key={def.id} className="module-tray__item" onClick={() => onAdd(def.id)}>
              + {def.name}
            </button>
          ))}
        </div>
        <button className="module-tray__reset" onClick={onReset}>RESET LAYOUT</button>
      </div>
    </div>
  );
}
