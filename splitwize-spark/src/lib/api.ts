const API_BASE = '/api';

export interface Participant {
  name: string;
  amount: string;
  status: 'pending' | 'paid';
  screenshot_path?: string;
}

export interface Session {
  id: string;
  event_name: string;
  bill_amount: string;
  payee: string;
  even_split: boolean;
  created_at: string;
  participants: Participant[];
}

export async function createSession(data: {
  event_name: string;
  bill_amount: string;
  payee: string;
  payee_phone?: string;
  payee_amount?: string;
  payee_telegram_id?: string;
  even_split: boolean;
  participants: { name: string; amount: string; telegram_id?: string }[];
  chat_id?: string;
  thread_id?: string;
}): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to create session: ${res.statusText}`);
  return res.json();
}

export async function getSession(id: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions/${id}`);
  if (!res.ok) throw new Error(`Failed to get session: ${res.statusText}`);
  return res.json();
}

export async function updatePaymentStatus(
  sessionId: string,
  participantName: string,
  status: 'paid' | 'pending',
  telegramId?: string,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/sessions/${sessionId}/participants/${encodeURIComponent(participantName)}/status`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, telegram_id: telegramId }),
    },
  );
  if (!res.ok) throw new Error(`Failed to update status: ${res.statusText}`);
}

export async function uploadScreenshot(
  sessionId: string,
  participantName: string,
  file: File,
): Promise<void> {
  const formData = new FormData();
  formData.append('screenshot', file);
  const res = await fetch(
    `${API_BASE}/sessions/${sessionId}/participants/${encodeURIComponent(participantName)}/screenshot`,
    { method: 'POST', body: formData },
  );
  if (!res.ok) throw new Error(`Failed to upload screenshot: ${res.statusText}`);
}

export async function sendReminders(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/remind`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to send reminders: ${res.statusText}`);
}

export async function setAutoRemind(
  sessionId: string,
  hours: number | null,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/sessions/${sessionId}/auto-remind`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(hours !== null ? { hours } : {}),
    },
  );
  if (!res.ok) throw new Error(`Failed to set auto-remind: ${res.statusText}`);
}

export async function scanReceipt(file: File): Promise<{
  items?: { name: string; price: number; qty: number }[];
  add_on_charges?: { name: string; price: number }[];
  informational_charges?: { name: string; price: number }[];
  charges?: { name: string; price: number }[];
  charges_included?: boolean;
  subtotal?: number;
  total?: number;
  computed_total?: number;
  receipt_grand_total?: number | null;
  error?: string;
}> {
  const formData = new FormData();
  formData.append('receipt', file);
  const res = await fetch(`${API_BASE}/ocr`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) throw new Error(`OCR failed: ${res.statusText}`);
  return res.json();
}

export function qrUrl(sessionId: string, participantName: string): string {
  return `${API_BASE}/sessions/${sessionId}/qr/${encodeURIComponent(participantName)}`;
}
