import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { EditTaskModal } from './src/EditTaskModal';
import { TaskRow } from './src/TaskRow';
import { formatDuration, totalTime, useTasks } from './src/tasks';
import { colors } from './src/theme';

type Filter = 'todo' | 'done' | 'all';

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'todo', label: 'To do' },
  { key: 'done', label: 'Done' },
  { key: 'all', label: 'All' },
];

function TaskScreen() {
  const { tasks, loaded, addTask, toggleTimer, toggleDone, setTime, rename, deleteTask } = useTasks();
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState<Filter>('todo');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  // Tick once a second only while some timer is running.
  const anyRunning = tasks.some((t) => t.timerStartedAt !== null);
  useEffect(() => {
    if (!anyRunning) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [anyRunning]);

  const visible = useMemo(
    () => tasks.filter((t) => (filter === 'all' ? true : filter === 'done' ? t.done : !t.done)),
    [tasks, filter],
  );
  const doneCount = tasks.filter((t) => t.done).length;
  const totalMs = tasks.reduce((sum, t) => sum + totalTime(t, now), 0);
  const editing = tasks.find((t) => t.id === editingId) ?? null;

  const submit = () => {
    const title = draft.trim();
    if (!title) return;
    addTask(title);
    setDraft('');
    setFilter((f) => (f === 'done' ? 'todo' : f));
  };

  return (
    <SafeAreaView style={styles.screen} edges={['top', 'left', 'right']}>
      <StatusBar style="dark" />

      <View style={styles.header}>
        <Text style={styles.heading}>My Tasks</Text>
        <Text style={styles.summary}>
          {doneCount} of {tasks.length} done · {formatDuration(totalMs)} tracked
        </Text>
      </View>

      <View style={styles.addRow}>
        <TextInput
          style={styles.addInput}
          placeholder="What do you need to do?"
          placeholderTextColor={colors.muted}
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={submit}
          returnKeyType="done"
        />
        <Pressable style={[styles.addBtn, !draft.trim() && styles.addBtnDisabled]} onPress={submit}>
          <Text style={styles.addBtnText}>Add</Text>
        </Pressable>
      </View>

      <View style={styles.filters}>
        {FILTERS.map((f) => (
          <Pressable
            key={f.key}
            onPress={() => setFilter(f.key)}
            style={[styles.filter, filter === f.key && styles.filterActive]}
          >
            <Text style={[styles.filterText, filter === f.key && styles.filterTextActive]}>{f.label}</Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={visible}
        keyExtractor={(t) => t.id}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        renderItem={({ item }) => (
          <TaskRow
            task={item}
            now={now}
            onToggleDone={() => toggleDone(item.id)}
            onToggleTimer={() => toggleTimer(item.id)}
            onOpen={() => setEditingId(item.id)}
          />
        )}
        ListEmptyComponent={
          loaded ? (
            <Text style={styles.empty}>
              {filter === 'done'
                ? 'Nothing finished yet.'
                : filter === 'todo' && tasks.length > 0
                  ? 'All caught up! 🎉'
                  : 'No tasks yet. Add one above.'}
            </Text>
          ) : null
        }
      />

      <EditTaskModal
        task={editing}
        onClose={() => setEditingId(null)}
        onSave={(id, title, ms) => {
          rename(id, title);
          setTime(id, ms);
        }}
        onDelete={deleteTask}
      />
    </SafeAreaView>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <TaskScreen />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8 },
  heading: { fontSize: 28, fontWeight: '800', color: colors.text },
  summary: { fontSize: 14, color: colors.muted, marginTop: 4 },
  addRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, marginTop: 8 },
  addInput: {
    flex: 1,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    color: colors.text,
  },
  addBtn: { backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 18, justifyContent: 'center' },
  addBtnDisabled: { opacity: 0.5 },
  addBtnText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  filters: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, marginTop: 16, marginBottom: 8 },
  filter: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: 20, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  filterActive: { backgroundColor: colors.text, borderColor: colors.text },
  filterText: { color: colors.text, fontWeight: '600', fontSize: 13 },
  filterTextActive: { color: '#fff' },
  list: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 40 },
  empty: { textAlign: 'center', color: colors.muted, marginTop: 40, fontSize: 15 },
});
