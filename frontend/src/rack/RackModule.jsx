import './RackModule.css';

/**
 * Wraps a registry module's body with its 20px rail (Section 1.4). This is
 * the element react-grid-layout positions; drag is restricted to the rail
 * via the grid's `draggableHandle` selector, so module bodies stay fully
 * interactive even while the rack is unlocked.
 */
export default function RackModule({ def, w, h, editMode, onRemove, collapseRailWhenLocked }) {
  const Comp = def.component;
  const railHidden = collapseRailWhenLocked && !editMode;
  return (
    <div className="rack-module">
      <div className={`rack-module__rail no-drag ${railHidden ? 'is-collapsed' : ''}`}>
        <span className="rack-module__grip" aria-hidden="true" />
        <span className="rack-module__name">{def.name}</span>
        {editMode && def.removable && (
          <button
            className="rack-module__remove"
            onClick={() => onRemove(def.id)}
            title="Remove module"
          >
            ✕
          </button>
        )}
      </div>
      <div className="rack-module__body no-drag">
        <Comp moduleId={def.id} w={w} h={h} />
      </div>
    </div>
  );
}
