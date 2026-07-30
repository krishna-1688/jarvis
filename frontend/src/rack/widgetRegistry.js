import ThinMeterModule from '../modules/widget/ThinMeterModule.jsx';
import NextClassModule from '../modules/widget/NextClassModule.jsx';
import ThinCountdownModule from '../modules/widget/ThinCountdownModule.jsx';
import ThinChipsModule from '../modules/widget/ThinChipsModule.jsx';
import NextTaskModule from '../modules/widget/NextTaskModule.jsx';
import FocusChipModule from '../modules/widget/FocusChipModule.jsx';

/** Widget's own rack (Section 1.4): 1 row x 6 cols, thin-capable modules only. */
export const WIDGET_MODULE_REGISTRY = {
  'thin-meter': {
    id: 'thin-meter',
    name: 'METER',
    allowedSizes: [{ w: 3, h: 1 }],
    removable: false,
    component: ThinMeterModule,
    dataSource: null,
  },
  'next-class': {
    id: 'next-class',
    name: 'NEXT CLASS',
    allowedSizes: [{ w: 2, h: 1 }],
    removable: true,
    component: NextClassModule,
    dataSource: 'timetable',
  },
  'thin-countdown': {
    id: 'thin-countdown',
    name: 'COUNTDOWN',
    allowedSizes: [{ w: 2, h: 1 }],
    removable: true,
    component: ThinCountdownModule,
    dataSource: 'exams',
  },
  'thin-chips': {
    id: 'thin-chips',
    name: 'STATUS',
    allowedSizes: [{ w: 1, h: 1 }],
    removable: true,
    component: ThinChipsModule,
    dataSource: null,
  },
  'next-task': {
    id: 'next-task',
    name: 'NEXT TASK',
    allowedSizes: [{ w: 1, h: 1 }],
    removable: true,
    component: NextTaskModule,
    dataSource: 'tasks',
  },
  'focus-chip': {
    id: 'focus-chip',
    name: 'FOCUS',
    allowedSizes: [{ w: 1, h: 1 }],
    removable: true,
    component: FocusChipModule,
    dataSource: 'focus',
  },
};

export const DEFAULT_WIDGET_LAYOUT = [
  { i: 'thin-meter', x: 0, y: 0, w: 3, h: 1 },
  { i: 'next-class', x: 3, y: 0, w: 2, h: 1 },
  { i: 'thin-chips', x: 5, y: 0, w: 1, h: 1 },
];
