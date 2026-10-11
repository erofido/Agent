import { useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { colors } from './theme';
import { Task, totalTime } from './tasks';

type Props = {
  task: Task | null;
  onClose: () => void;
  onSave: (id: string, title: string, ms: number) => void;
  onDelete: (id: string) => void;
};

export function EditTaskModal({ task, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState('');
  const [hours, setHours] = useState('');
  const [minutes, setMinutes] = useState('');

  useEffect(() => {
    if (!task) return;
    const totalMinutes = Math.round(totalTime(task, Date.now()) / 60000);
    setTitle(task.title);
    setHours(String(Math.floor(totalMinutes / 60)));
    setMinutes(String(totalMinutes % 60));
  }, [task]);

  if (!task) return null;

  const save = () => {
    const h = parseInt(hours, 10) || 0;
    const m = parseInt(minutes, 10) || 0;
    const originalMinutes = Math.round(totalTime(task, Date.now()) / 60000);
    // Only overwrite the time if it was actually edited, so seconds aren't lost on a rename.
    const ms = h * 60 + m === originalMinutes ? totalTime(task, Date.now()) : (h * 60 + m) * 60000;
    onSave(task.id, title.trim() || task.title, ms);
    onClose();
  };

  const confirmDelete = () => {
    const remove = () => {
      onDelete(task.id);
      onClose();
    };
    if (Platform.OS === 'web') {
      if (window.confirm(`Delete "${task.title}"?`)) remove();
      return;
    }
    Alert.alert('Delete task', `Delete "${task.title}"?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: remove },
    ]);
  };

  return (
    <Modal visible transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.backdrop}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={styles.sheet}>
          <Text style={styles.heading}>Edit task</Text>

          <Text style={styles.label}>Name</Text>
          <TextInput style={styles.input} value={title} onChangeText={setTitle} />

          <Text style={styles.label}>Time it took</Text>
          <View style={styles.timeRow}>
            <TextInput
              style={[styles.input, styles.timeInput]}
              value={hours}
              onChangeText={(v) => setHours(v.replace(/[^0-9]/g, ''))}
              keyboardType="number-pad"
            />
            <Text style={styles.unit}>h</Text>
            <TextInput
              style={[styles.input, styles.timeInput]}
              value={minutes}
              onChangeText={(v) => setMinutes(v.replace(/[^0-9]/g, ''))}
              keyboardType="number-pad"
            />
            <Text style={styles.unit}>min</Text>
          </View>

          <View style={styles.actions}>
            <Pressable onPress={confirmDelete} style={styles.deleteBtn}>
              <Text style={styles.deleteText}>Delete</Text>
            </Pressable>
            <View style={{ flex: 1 }} />
            <Pressable onPress={onClose} style={styles.secondaryBtn}>
              <Text style={styles.secondaryText}>Cancel</Text>
            </Pressable>
            <Pressable onPress={save} style={styles.primaryBtn}>
              <Text style={styles.primaryText}>Save</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.35)' },
  sheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    paddingBottom: 36,
  },
  heading: { fontSize: 18, fontWeight: '700', color: colors.text, marginBottom: 12 },
  label: { fontSize: 13, color: colors.muted, marginTop: 12, marginBottom: 6 },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    color: colors.text,
  },
  timeRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  timeInput: { width: 70, textAlign: 'center' },
  unit: { fontSize: 15, color: colors.text, marginRight: 8 },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 24 },
  deleteBtn: { paddingVertical: 10, paddingHorizontal: 4 },
  deleteText: { color: colors.danger, fontSize: 15, fontWeight: '600' },
  secondaryBtn: { paddingVertical: 10, paddingHorizontal: 14 },
  secondaryText: { color: colors.muted, fontSize: 15, fontWeight: '600' },
  primaryBtn: { backgroundColor: colors.accent, borderRadius: 10, paddingVertical: 10, paddingHorizontal: 18 },
  primaryText: { color: '#fff', fontSize: 15, fontWeight: '600' },
});
