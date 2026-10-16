// What the API hands out: each item in the shape the bot stores it in
// (schedule_provider.task_to_dict and its siblings), and takes back the same.

export interface Item {
  id: number;
  name: string;
}

export interface Task extends Item {
  description: string;
  duration: number;
  priority: number;
  deadline: string | null;
  max_chunk_duration: number | null;
  min_chunk_duration: number | null;
  break_duration: number;
  depends_on: number[];
}

export interface Routine extends Item {
  type: "fixed" | "flexible";
  repeat: "daily" | "weekly";
  duration: number;
  time: string | null;
  deadline_time: string | null;
  weekdays: number[] | null;
  priority: number;
  break_duration: number;
  depends_on: number[];
  resume_after: string | null;
}

export interface TimeBlock extends Item {
  repeat: "once" | "daily" | "weekly";
  start: string;
  end: string;
  weekdays?: number[];
}

export interface Limits {
  max_items?: number;
  name_length?: number;
  description_length?: number;
}

export type Defaults = Record<string, number | null>;

export interface Listing {
  limits?: Limits;
  defaults?: Defaults;
  [list: string]: unknown;
}

export interface Me {
  id: string;
  name: string;
}
