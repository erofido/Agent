import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors } from './theme';
import { formatDuration, Task, totalTime } from './tasks';

type Props = {
  task: Task;
  now: number;
  onToggleDone: () => void;
  onToggleTimer: () => void;
  onOpen: () => void;
};

export function TaskRow({ task, now, onToggleDone, onToggleTimer, onOpen }: Props) {
  const running = task.timerStartedAt !== null;

  return (
    <Pressable style={[styles.row, running && styles.rowRunning]} onPress={onOpen}>
      <Pressable
        onPress={onToggleDone}
        hitSlop={10}
        style={[styles.check, task.done && styles.checkDone]}
        accessibilityRole="checkbox"
        accessibilityState={{ checked: task.done }}
        accessibilityLabel={task.done ? 'Mark as not done' : 'Mark as done'}
      >
        {task.done && <Text style={styles.checkMark}>✓</Text>}
      </Pressable>

      <View style={styles.body}>
        <Text style={[styles.title, task.done && styles.titleDone]} numberOfLines={2}>
          {task.title}
        </Text>
        <Text style={styles.meta}>
          {task.done && task.completedAt
            ? `Done ${new Date(task.completedAt).toLocaleDateString()}`
            : running
              ? 'Timer running…'
              : 'Not done yet'}
        </Text>
      </View>

      <Text style={[styles.time, running && styles.timeRunning]}>{formatDuration(totalTime(task, now))}</Text>

      {!task.done && (
        <Pressable
          onPress={onToggleTimer}
          hitSlop={8}
          style={[styles.timerBtn, running && styles.timerBtnRunning]}
          accessibilityLabel={running ? 'Pause timer' : 'Start timer'}
        >
          <Text style={[styles.timerBtnText, running && styles.timerBtnTextRunning]}>{running ? '❚❚' : '▶'}</Text>
        </Pressable>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: colors.card,
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: colors.border,
  },
  rowRunning: { borderColor: colors.accent },
  check: {
    width: 26,
    height: 26,
    borderRadius: 13,
    borderWidth: 2,
    borderColor: colors.muted,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkDone: { backgroundColor: colors.success, borderColor: colors.success },
  checkMark: { color: '#fff', fontWeight: '700', fontSize: 15 },
  body: { flex: 1 },
  title: { fontSize: 16, color: colors.text, fontWeight: '500' },
  titleDone: { color: colors.muted, textDecorationLine: 'line-through' },
  meta: { fontSize: 12, color: colors.muted, marginTop: 2 },
  time: { fontSize: 14, color: colors.text, fontVariant: ['tabular-nums'] },
  timeRunning: { color: colors.accent, fontWeight: '600' },
  timerBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.accentSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  timerBtnRunning: { backgroundColor: colors.accent },
  timerBtnText: { color: colors.accent, fontSize: 13, fontWeight: '700' },
  timerBtnTextRunning: { color: '#fff' },
});
