import AttendanceModule from '../modules/AttendanceModule/AttendanceModule.jsx';
import ScheduleModule from '../modules/ScheduleModule/ScheduleModule.jsx';
import ExamCountdownModule from '../modules/ExamCountdownModule/ExamCountdownModule.jsx';
import AssignmentsModule from '../modules/AssignmentsModule/AssignmentsModule.jsx';
import StatusChipsModule from '../modules/StatusChipsModule/StatusChipsModule.jsx';
import VUCoreModule from '../modules/VUCoreModule/VUCoreModule.jsx';
import TasksModule from '../modules/TasksModule/TasksModule.jsx';
import FocusModule from '../modules/FocusModule/FocusModule.jsx';
import ExpenseModule from '../modules/ExpenseModule/ExpenseModule.jsx';

/**
 * Adding a future module = one entry here. `allowedSizes` are the only
 * w×h presets a module may snap to when resized (Section 1.4) — the first
 * entry is the default/initial size.
 *
 * @typedef {{ id: string, name: string, allowedSizes: {w:number,h:number}[], removable: boolean, component: React.ComponentType, dataSource?: string }} ModuleDef
 * @type {Record<string, ModuleDef>}
 */
export const MODULE_REGISTRY = {
  'vu-core': {
    id: 'vu-core',
    name: 'VU METER · TELEPRINTER · LOG · COMMAND',
    allowedSizes: [{ w: 6, h: 6 }, { w: 4, h: 4 }],
    removable: false,
    component: VUCoreModule,
    dataSource: null,
  },
  schedule: {
    id: 'schedule',
    name: 'SCHEDULE',
    allowedSizes: [{ w: 3, h: 6 }, { w: 2, h: 4 }],
    removable: true,
    component: ScheduleModule,
    dataSource: 'schedule',
  },
  attendance: {
    id: 'attendance',
    name: 'ATTENDANCE',
    allowedSizes: [{ w: 3, h: 4 }, { w: 5, h: 6 }],
    removable: true,
    component: AttendanceModule,
    dataSource: 'attendance',
  },
  'exam-countdown': {
    id: 'exam-countdown',
    name: 'EXAM COUNTDOWN',
    allowedSizes: [{ w: 3, h: 2 }],
    removable: true,
    component: ExamCountdownModule,
    dataSource: 'exams',
  },
  assignments: {
    id: 'assignments',
    name: 'ASSIGNMENTS',
    allowedSizes: [{ w: 6, h: 2 }, { w: 3, h: 4 }],
    removable: true,
    component: AssignmentsModule,
    dataSource: 'assignments',
  },
  'status-chips': {
    id: 'status-chips',
    name: 'STATUS',
    allowedSizes: [{ w: 3, h: 1 }],
    removable: true,
    component: StatusChipsModule,
    dataSource: null,
  },
  tasks: {
    id: 'tasks',
    name: 'TASKS',
    allowedSizes: [{ w: 3, h: 4 }, { w: 5, h: 6 }],
    removable: true,
    component: TasksModule,
    dataSource: 'tasks',
  },
  focus: {
    id: 'focus',
    name: 'FOCUS',
    allowedSizes: [{ w: 3, h: 4 }],
    removable: true,
    component: FocusModule,
    dataSource: 'focus',
  },
  expenses: {
    id: 'expenses',
    name: 'EXPENSES',
    allowedSizes: [{ w: 3, h: 4 }, { w: 6, h: 4 }],
    removable: true,
    component: ExpenseModule,
    dataSource: 'expenses',
  },
};

export const DEFAULT_CONSOLE_LAYOUT = [
  { i: 'schedule',       x: 0, y: 0, w: 3, h: 6 },
  { i: 'vu-core',        x: 3, y: 0, w: 6, h: 6 },
  { i: 'attendance',     x: 9, y: 0, w: 3, h: 4 },
  { i: 'tasks',          x: 9, y: 4, w: 3, h: 4 },
  { i: 'assignments',    x: 0, y: 6, w: 6, h: 2 },
  { i: 'exam-countdown', x: 6, y: 6, w: 3, h: 2 },
];
