import AsyncStorage from '@react-native-async-storage/async-storage';
import { useCallback, useEffect, useState } from 'react';

export type Task = {
  id: string;
  title: string;
  createdAt: number;
  done: boolean;
  completedAt: number | null;
  // Time already counted, not including a timer that is currently running.
  elapsedMs: number;
  // When the running timer was started, or null if the timer is stopped.
  timerStartedAt: number | null;
};

const STORAGE_KEY = 'task-tracker/tasks/v1';

export function totalTime(task: Task, now: number): number {
  return task.elapsedMs + (task.timerStartedAt ? now - task.timerStartedAt : 0);
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

function stopTimer(task: Task, now: number): Task {
  if (!task.timerStartedAt) return task;
  return { ...task, elapsedMs: totalTime(task, now), timerStartedAt: null };
}

export function useTasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY)
      .then((raw) => {
        if (raw) setTasks(JSON.parse(raw));
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    // Don't overwrite saved tasks with the empty initial list before loading finishes.
    if (loaded) AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(tasks)).catch(() => {});
  }, [tasks, loaded]);

  const update = useCallback((id: string, fn: (task: Task, now: number) => Task) => {
    const now = Date.now();
    setTasks((prev) => prev.map((t) => (t.id === id ? fn(t, now) : t)));
  }, []);

  const addTask = useCallback((title: string) => {
    const now = Date.now();
    setTasks((prev) => [
      {
        id: `${now}-${Math.random().toString(36).slice(2, 8)}`,
        title,
        createdAt: now,
        done: false,
        completedAt: null,
        elapsedMs: 0,
        timerStartedAt: null,
      },
      ...prev,
    ]);
  }, []);

  const toggleTimer = useCallback(
    (id: string) =>
      update(id, (t, now) =>
        t.timerStartedAt ? stopTimer(t, now) : { ...t, done: false, completedAt: null, timerStartedAt: now },
      ),
    [update],
  );

  const toggleDone = useCallback(
    (id: string) =>
      update(id, (t, now) =>
        t.done
          ? { ...t, done: false, completedAt: null }
          : { ...stopTimer(t, now), done: true, completedAt: now },
      ),
    [update],
  );

  const setTime = useCallback(
    (id: string, ms: number) => update(id, (t, now) => ({ ...t, elapsedMs: ms, timerStartedAt: t.timerStartedAt ? now : null })),
    [update],
  );

  const rename = useCallback((id: string, title: string) => update(id, (t) => ({ ...t, title })), [update]);

  const deleteTask = useCallback((id: string) => setTasks((prev) => prev.filter((t) => t.id !== id)), []);

  return { tasks, loaded, addTask, toggleTimer, toggleDone, setTime, rename, deleteTask };
}
