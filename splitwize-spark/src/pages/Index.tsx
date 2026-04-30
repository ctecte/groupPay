import { useState, useEffect, useRef, useCallback } from 'react';
import { Camera, Users, DollarSign, CheckCircle, XCircle, Clock, QrCode, ArrowRight, ArrowLeft, Edit2, Bell, X } from 'lucide-react';
import { createSession, getSession, updatePaymentStatus, uploadScreenshot, sendReminders, qrUrl, scanReceipt, setAutoRemind } from '@/lib/api';

export default function GroupPayPrototype() {
  const [step, setStep] = useState('start');
  const [billAmount, setBillAmount] = useState('');
  const [participants, setParticipants] = useState(['']);
  const [evenSplit, setEvenSplit] = useState<boolean | null>(null);
  const [customAmounts, setCustomAmounts] = useState<Record<string, string>>({});
  const [splitConfirmed, setSplitConfirmed] = useState(false);
  const [paymentStatuses, setPaymentStatuses] = useState<Record<string, string>>({});
  const [selectedPayer, setSelectedPayer] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState('you');
  const [payeeChoice, setPayeeChoice] = useState<'me' | 'other'>('me');
  const [eventName, setEventName] = useState('dinner');
  const [ocrScanning, setOcrScanning] = useState(false);
  const [ocrItems, setOcrItems] = useState<{ name: string; price: number; qty: number }[]>([]);
  const [ocrCharges, setOcrCharges] = useState<{ name: string; price: number }[]>([]);
  const [ocrChargesIncluded, setOcrChargesIncluded] = useState(false);
  const [ocrSubtotal, setOcrSubtotal] = useState(0);
  const [itemAssignments, setItemAssignments] = useState<Record<number, string[]>>({});
  const [ocrEditing, setOcrEditing] = useState(false);
  const [ocrError, setOcrError] = useState<string | null>(null);
  const galleryInputRef = useRef<HTMLInputElement>(null);
  const [uploadingScreenshot, setUploadingScreenshot] = useState(false);
  const [screenshotUploaded, setScreenshotUploaded] = useState(false);
  const [screenshotUrls, setScreenshotUrls] = useState<Record<string, string | null>>({});
  const [viewingScreenshot, setViewingScreenshot] = useState<string | null>(null);
  const [verifyingPayment, setVerifyingPayment] = useState(false);
  const [remindersSent, setRemindersSent] = useState<Record<string, string>>({});
  const [autoRemindHours, setAutoRemindHours] = useState<number | null>(null);
  const [showRemindPicker, setShowRemindPicker] = useState(false);
  const [sendingReminder, setSendingReminder] = useState<string | null>(null);
  const [payeePhone, setPayeePhone] = useState(() => {
    // Auto-fill from localStorage if user has paid before
    try {
      const cached = localStorage.getItem('grouppay_phone');
      if (cached && cached.length === 8 && /^[89]/.test(cached)) return cached;
    } catch {}
    return '';
  });

  // Tabbed views
  const [reviewTab, setReviewTab] = useState<'overview' | 'details'>('overview');
  const [overviewTab, setOverviewTab] = useState<'status' | 'history'>('status');

  // Payment history
  const [paymentHistory, setPaymentHistory] = useState<{ name: string; amount: string; time: string }[]>([]);

  // GST state
  const [serviceChargeOn, setServiceChargeOn] = useState(false);
  const [gstOn, setGstOn] = useState(false);
  const [serviceChargeRate, setServiceChargeRate] = useState(10);
  const [gstRate, setGstRate] = useState(9);

  // Split method state
  const [splitMethod, setSplitMethod] = useState<'amount' | 'percentage' | 'shares'>('amount');
  const [customPercentages, setCustomPercentages] = useState<Record<string, string>>({});
  const [customShares, setCustomShares] = useState<Record<string, string>>({});
  const [menuPriceMode, setMenuPriceMode] = useState(true);
  const [customItemLines, setCustomItemLines] = useState<Record<string, string[]>>({});

  // Telegram state
  const [isTMA, setIsTMA] = useState(false);
  const [myTelegramId, setMyTelegramId] = useState<string | null>(null);
  const [sessionPayeeTid, setSessionPayeeTid] = useState<string | null>(null);
  const [viewerName, setViewerName] = useState<string | null>(null); // The actual person viewing (from Telegram)

  // Known group members from bot (parsed from URL ?members=Name1:id1,Name2:id2)
  const [knownMembers, setKnownMembers] = useState<{ name: string; id: string }[]>([]);

  // Session ID for API
  const [sessionId, setSessionId] = useState<string | null>(null);

  // File input ref for screenshot upload
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Detect Telegram Mini App environment, parse members & session from URL
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    let tgUserId: string | undefined;
    let tgName: string | undefined;

    if (tg) {
      setIsTMA(true);
      tg.ready();
      tg.expand();

      const tgUser = tg.initDataUnsafe?.user;
      if (tgUser) {
        setCurrentUser(tgUser.first_name);
        setViewerName(tgUser.first_name);
        setPayeeChoice('me');
        tgUserId = tgUser.id?.toString();
        tgName = tgUser.first_name;
        if (tgUserId) setMyTelegramId(tgUserId);
      }
    }

    const params = new URLSearchParams(window.location.search);

    // Restore session from URL
    const sessionParam = params.get('session');
    if (sessionParam) {
      setSessionId(sessionParam);
      getSession(sessionParam).then(session => {
        setEventName(session.event_name);
        setBillAmount(session.bill_amount);
        setCurrentUser(session.payee);
        if (session.payee_telegram_id) setSessionPayeeTid(session.payee_telegram_id);
        setEvenSplit(session.even_split);
        const names = session.participants.map(p => p.name);
        setParticipants(names);
        const statuses: Record<string, string> = {};
        const amounts: Record<string, string> = {};
        const screenshots: Record<string, string | null> = {};
        session.participants.forEach(p => {
          statuses[p.name] = p.status;
          amounts[p.name] = p.amount;
          screenshots[p.name] = p.screenshot_url || null;
        });
        setPaymentStatuses(statuses);
        setCustomAmounts(amounts);
        setScreenshotUrls(screenshots);
        if (session.remind_after_hours) setAutoRemindHours(session.remind_after_hours);
        setSplitConfirmed(true);
        setStep('overview');
      }).catch(() => {
        // Session not found, start fresh
      });
    }

    // Parse ?members= from URL
    const membersParam = params.get('members');
    if (membersParam) {
      const parsed = membersParam.split(',').map(entry => {
        const lastColon = entry.lastIndexOf(':');
        if (lastColon === -1) return { name: decodeURIComponent(entry), id: '' };
        const name = entry.slice(0, lastColon);
        const id = entry.slice(lastColon + 1);
        return { name: decodeURIComponent(name), id: id || '' };
      }).filter(m => m.name);
      setKnownMembers(parsed);

      // If in TMA, find our own entry by Telegram ID and set it
      // (even if user changes their display name later)
      if (tgUserId) {
        const me = parsed.find(m => m.id === tgUserId);
        if (me) setMyTelegramId(tgUserId);
      }
    }
  }, []);

  // Resolve the payer's Telegram ID from name match or TMA
  const payerTelegramId = myTelegramId || knownMembers.find(m => m.name === currentUser)?.id || null;

  // Check if a participant is the payer (by Telegram ID or name)
  const isPayerParticipant = (name: string) => {
    if (name === currentUser) return true;
    if (payerTelegramId) {
      const member = knownMembers.find(m => m.name === name);
      if (member && member.id === payerTelegramId) return true;
    }
    return false;
  };

  // Participants excluding the payer
  const otherParticipants = participants.filter(p => p.trim() && !isPayerParticipant(p));

  // Check if the person viewing is the payee
  const isViewerThePayee = (() => {
    // By Telegram ID against stored session payee ID (most reliable for loaded sessions)
    if (myTelegramId && sessionPayeeTid && myTelegramId === sessionPayeeTid) return true;
    // By Telegram ID against resolved payee ID (for session creator flow)
    if (myTelegramId && payerTelegramId && myTelegramId === payerTelegramId) return true;
    // Fallback: viewer name matches payee name (for session creator before confirming)
    if (viewerName && viewerName === currentUser) return true;
    return false;
  })();

  // Bill is always the grand total
  const finalBillAmount = parseFloat(billAmount) || 0;
  const finalBillStr = finalBillAmount.toFixed(2);

  // ++ multiplier from user-selected rates (for menu price mode)
  const hasCharges = serviceChargeOn || gstOn;
  const scFactor = serviceChargeOn ? (1 + serviceChargeRate / 100) : 1;
  const gstFactor = gstOn ? (1 + gstRate / 100) : 1;
  const ppMultiplier = scFactor * gstFactor;
  const impliedSubtotal = hasCharges ? finalBillAmount / ppMultiplier : finalBillAmount;
  const impliedSC = serviceChargeOn ? impliedSubtotal * (serviceChargeRate / 100) : 0;
  const impliedGST = gstOn ? (impliedSubtotal + impliedSC) * (gstRate / 100) : 0;
  const useMenuPriceMode = menuPriceMode && hasCharges && splitMethod === 'amount';

  // Sum item lines for a person (menu prices)
  const getItemLinesSum = (person: string) => {
    const lines = customItemLines[person] || [''];
    return lines.reduce((sum, v) => sum + (parseFloat(v) || 0), 0);
  };

  const calculateSplitAmount = () => {
    const total = otherParticipants.length + 1;
    return (finalBillAmount / total).toFixed(2);
  };

  const getAmountFromPercentage = (person: string) => {
    const pct = parseFloat(customPercentages[person] || '0');
    return ((pct / 100) * finalBillAmount).toFixed(2);
  };

  const getAmountFromShares = (person: string) => {
    const allKeys = [currentUser, ...otherParticipants];
    const totalShares = allKeys.reduce((sum, k) => sum + (parseFloat(customShares[k] || '0') || 0), 0);
    if (totalShares === 0) return '0.00';
    const myShares = parseFloat(customShares[person] || '0') || 0;
    return ((myShares / totalShares) * finalBillAmount).toFixed(2);
  };

  const getResolvedAmount = (person: string) => {
    if (evenSplit) return calculateSplitAmount();
    if (splitMethod === 'percentage') return getAmountFromPercentage(person);
    if (splitMethod === 'shares') return getAmountFromShares(person);
    if (useMenuPriceMode) {
      const menuTotal = getItemLinesSum(person);
      return (menuTotal * ppMultiplier).toFixed(2);
    }
    return customAmounts[person] || '0.00';
  };

  // Settlement progress
  const paidCount = participants.filter(p => p.trim() && paymentStatuses[p] === 'paid').length;
  const totalParticipants = otherParticipants.length;
  const paidAmount = participants.filter(p => p.trim() && paymentStatuses[p] === 'paid')
    .reduce((sum, p) => sum + parseFloat(getResolvedAmount(p)), 0);
  const settlementPct = totalParticipants > 0 ? (paidCount / totalParticipants) * 100 : 0;
  const leftToPayPct = 100 - settlementPct;

  const handleOCRScan = async (file: File) => {
    setOcrScanning(true);
    setOcrError(null);
    try {
      const result = await scanReceipt(file);
      if (result.error || !result.items?.length) {
        setOcrError(result.error || 'No items detected. Try a clearer photo.');
        setOcrScanning(false);
        return;
      }
      setOcrItems(result.items);
      setOcrCharges(result.charges ?? []);
      setOcrChargesIncluded(result.charges_included ?? false);
      setOcrSubtotal(result.subtotal ?? 0);
      setBillAmount((result.total ?? 0).toFixed(2));
      setItemAssignments({});
      setOcrEditing(false);
      setOcrScanning(false);
      setStep('ocr-result');
    } catch (e) {
      console.error('[OCR] Error:', e);
      alert(`OCR error: ${e}`);
      setOcrError('Failed to process receipt. Please try again.');
      setOcrScanning(false);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleOCRScan(file);
    e.target.value = '';
  };

  const handleScreenshotUpload = async (file: File) => {
    if (!sessionId || !selectedPayer) return;
    setUploadingScreenshot(true);
    try {
      await uploadScreenshot(sessionId, selectedPayer, file);
      setUploadingScreenshot(false);
      setScreenshotUploaded(true);
      setStep('verify-payment');
    } catch {
      setUploadingScreenshot(false);
    }
  };

  const handlePaymentVerification = async () => {
    if (!sessionId || !selectedPayer) return;
    setVerifyingPayment(true);
    try {
      await updatePaymentStatus(sessionId, selectedPayer, 'paid', myTelegramId || undefined);
      confirmPayment(selectedPayer);
      setVerifyingPayment(false);
      setScreenshotUploaded(false);
      setStep('payment-confirmed');
    } catch {
      setVerifyingPayment(false);
    }
  };

  const addParticipant = () => {
    setParticipants([...participants, '']);
  };

  const updateParticipant = (index: number, value: string) => {
    const newParticipants = [...participants];
    newParticipants[index] = value.replace(/^@+/, '');
    setParticipants(newParticipants);
  };

  const removeParticipant = (index: number) => {
    if (participants.length > 1) {
      setParticipants(participants.filter((_, i) => i !== index));
    }
  };

  const calculateSplit = () => calculateSplitAmount();

  const generateQR = (participant: string) => {
    setSelectedPayer(participant);
    setStep('qr-display');
  };

  const confirmPayment = (participant: string) => {
    setPaymentStatuses(prev => ({ ...prev, [participant]: 'paid' }));
    setPaymentHistory(prev => [...prev, { name: participant, amount: getResolvedAmount(participant), time: new Date().toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', hour12: false }) }]);
  };

  const markAllAsPaid = async () => {
    const unpaid = participants.filter(p => p.trim() && paymentStatuses[p] !== 'paid');
    const updated = { ...paymentStatuses };
    const newHistory = [...paymentHistory];
    for (const p of unpaid) {
      updated[p] = 'paid';
      newHistory.push({ name: p, amount: getResolvedAmount(p), time: new Date().toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', hour12: false }) });
      if (sessionId) {
        try { await updatePaymentStatus(sessionId, p, 'paid', myTelegramId || undefined); } catch {}
      }
    }
    setPaymentStatuses(updated);
    setPaymentHistory(newHistory);
  };

  const allPaid = () => {
    const validParticipants = participants.filter(p => p.trim());
    return validParticipants.every(p => paymentStatuses[p] === 'paid');
  };

  const reset = () => {
    setStep('start');
    setBillAmount('');
    setParticipants(['']);
    setEvenSplit(null);
    setCustomAmounts({});
    setSplitConfirmed(false);
    setPaymentStatuses({});
    setSelectedPayer(null);
    setOcrScanning(false);
    setOcrItems([]);
    setUploadingScreenshot(false);
    setScreenshotUploaded(false);
    setVerifyingPayment(false);
    setRemindersSent({});
    setSendingReminder(null);
    setPayeeChoice('me');
    setPayeePhone('');
    setEventName('dinner');
    setBillType('total');
    setServiceChargeOn(false);
    setGstOn(false);
    setServiceChargeRate(10);
    setGstRate(9);
    setSplitMethod('amount');
    setCustomPercentages({});
    setCustomShares({});
    setShowGstBreakdown(true);
    setReviewTab('overview');
    setOverviewTab('status');
    setPaymentHistory([]);
    setSessionId(null);
  };

  const handleSendReminder = async (participant: string) => {
    setSendingReminder(participant);
    if (sessionId) {
      try { await sendReminders(sessionId); } catch {}
    }
    setTimeout(() => {
      setRemindersSent(prev => ({ ...prev, [participant]: new Date().toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' }) }));
      setSendingReminder(null);
    }, 1200);
  };

  const handleSendAllReminders = async () => {
    const unpaid = participants.filter(p => p.trim() && paymentStatuses[p] !== 'paid' && !remindersSent[p]);
    if (sessionId) {
      try { await sendReminders(sessionId); } catch {}
    }
    unpaid.forEach((p, i) => {
      setTimeout(() => {
        setRemindersSent(prev => ({ ...prev, [p]: new Date().toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' }) }));
      }, 800 * (i + 1));
    });
  };

  const handleConfirmSplit = async () => {
    setSplitConfirmed(true);
    setShowRemindPicker(false);
    setAutoRemindHours(null);
    setStep('auto-remind-setup');

    // Create session via API
    const validParticipants = participants.filter(p => p.trim());
    const participantData = validParticipants.map(p => {
      const member = knownMembers.find(m => m.name === p);
      return {
        name: p,
        amount: getResolvedAmount(p),
        telegram_id: member?.id || undefined,
      };
    });

    // Get chat_id and thread_id from URL if available
    const params = new URLSearchParams(window.location.search);
    const chatId = params.get('chat_id') || undefined;
    const threadId = params.get('thread_id') || undefined;

    try {
      const session = await createSession({
        event_name: eventName,
        bill_amount: finalBillStr,
        payee: currentUser,
        payee_phone: payeePhone,
        payee_amount: getResolvedAmount(currentUser),
        payee_telegram_id: myTelegramId || undefined,
        even_split: !!evenSplit,
        participants: participantData,
        chat_id: chatId,
        thread_id: threadId,
      });
      setSessionId(session.id);
      // Update URL so this session is bookmarkable/revisitable
      const url = new URL(window.location.href);
      url.searchParams.set('session', session.id);
      window.history.replaceState({}, '', url.toString());
    } catch {
      // API might not be available, continue with local state
    }
  };

  // If not in TMA, show "Open via Telegram" message
  if (!isTMA) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 flex items-center justify-center p-4">
        <div className="bg-white/5 backdrop-filter backdrop-blur-lg border border-white/10 rounded-3xl p-8 max-w-sm text-center">
          <div className="w-20 h-20 mx-auto mb-4 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center">
            <DollarSign className="text-white" size={40} />
          </div>
          <h1 className="text-white text-2xl font-bold mb-2">GroupPay</h1>
          <p className="text-blue-200 text-sm mb-6">Split bills, stay friends</p>
          <p className="text-white/60 text-sm">
            Open this app via Telegram to get started. Use the <span className="text-blue-300 font-semibold">/split</span> command in any group chat with the GroupPay bot.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 p-4 font-sans">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap');
        .app-content * { font-family: 'DM Sans', sans-serif; }
        .mono { font-family: 'JetBrains Mono', monospace; }
        @keyframes slideIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes shimmer { 0% { background-position: -1000px 0; } 100% { background-position: 1000px 0; } }
        .animate-in { animation: slideIn 0.4s ease-out; }
        .shimmer { background: linear-gradient(90deg, rgba(255,255,255,0.0) 0%, rgba(255,255,255,0.1) 50%, rgba(255,255,255,0.0) 100%); background-size: 1000px 100%; animation: shimmer 2s infinite; }
        .glass { background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); }
        .btn-primary { background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); transition: all 0.3s ease; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 10px 25px rgba(59, 130, 246, 0.4); }
        .btn-secondary { background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); transition: all 0.3s ease; }
        .btn-secondary:hover { background: rgba(255, 255, 255, 0.15); border-color: rgba(255, 255, 255, 0.3); }
      `}</style>

      {/* Hidden file input for screenshot upload */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleScreenshotUpload(file);
        }}
      />

      {/* Header */}
      <div className="max-w-md mx-auto mb-6 animate-in">
        <div className="glass rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center">
                <DollarSign className="text-white" size={24} />
              </div>
              <div>
                <h1 className="text-white text-xl font-bold">GroupPay</h1>
                <p className="text-blue-200 text-xs">Split bills, stay friends</p>
              </div>
            </div>
            <button onClick={reset} className="text-blue-200 hover:text-white text-sm px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 transition">Reset</button>
          </div>
        </div>
      </div>

      <div className="max-w-md mx-auto">
        {/* Start */}
        {step === 'start' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="text-center mb-8">
              <div className="w-20 h-20 mx-auto mb-4 rounded-2xl bg-gradient-to-br from-green-400 to-blue-500 flex items-center justify-center"><Users className="text-white" size={40} /></div>
              <h2 className="text-white text-2xl font-bold mb-2">Start Bill Splitting</h2>
              <p className="text-blue-200 text-sm">Quick, fair, transparent</p>
            </div>
            <div className="space-y-3">
              <button onClick={() => setStep('ocr-choice')} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"><span>Let's split a bill</span><ArrowRight size={20} /></button>
              <div className="pt-4 border-t border-white/10">
                <p className="text-blue-200 text-xs text-center mb-3">Why GroupPay?</p>
                <div className="space-y-2">
                  {['No app downloads needed', 'Verified payment confirmations', 'Zero awkward follow-ups'].map(t => (
                    <div key={t} className="flex items-center gap-2 text-white/80 text-sm"><CheckCircle size={16} className="text-green-400" /><span>{t}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* OCR Choice */}
        {step === 'ocr-choice' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-6">How do you want to enter the bill?</h2>
            <div className="space-y-3">
              <button onClick={() => setStep('ocr-scan')} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"><div className="flex items-center gap-3"><Camera size={20} /><span>Scan Receipt (OCR)</span></div><ArrowRight size={20} /></button>
              <button onClick={() => setStep('manual-bill')} className="w-full btn-secondary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"><div className="flex items-center gap-3"><Edit2 size={20} /><span>Enter Manually</span></div><ArrowRight size={20} /></button>
            </div>
            <button onClick={() => setStep('start')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
        )}

        {/* OCR Scan */}
        {step === 'ocr-scan' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">Scan Your Receipt</h2>
            <p className="text-blue-200 text-sm mb-6">Upload a clear image of your receipt, and try again if the scan is incorrect</p>
            <input ref={galleryInputRef} type="file" accept="image/*" onChange={handleFileSelect} className="hidden" />
            <div className="relative bg-black/40 rounded-2xl p-4 mb-6 aspect-[3/4] flex items-center justify-center border-2 border-dashed border-blue-400/50">
              {!ocrScanning ? (
                <div className="text-center">
                  <Camera className="text-blue-400 mx-auto mb-4" size={64} />
                  <p className="text-white/70 text-sm mb-6">Please upload a clear image of the receipt</p>
                  <button onClick={() => galleryInputRef.current?.click()} className="btn-primary text-white px-8 py-3 rounded-xl font-semibold w-full">📁 Upload Receipt</button>
                  {ocrError && (
                    <div className="mt-4 bg-red-500/10 rounded-xl p-3 border border-red-400/30">
                      <p className="text-red-200 text-sm">{ocrError}</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center">
                  <div className="w-16 h-16 border-4 border-blue-400 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
                  <p className="text-white font-semibold mb-2">Scanning receipt...</p>
                  <p className="text-blue-200 text-sm">Extracting items and amounts</p>
                </div>
              )}
            </div>
            {!ocrScanning && <button onClick={() => setStep('ocr-choice')} className="w-full text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>}
          </div>
        )}

        {/* OCR Result — readonly with edit toggle */}
        {step === 'ocr-result' && (() => {
          const updateItem = (index: number, field: 'name' | 'price' | 'qty', value: string) => {
            setOcrItems(prev => prev.map((item, i) => i === index ? {
              ...item,
              [field]: field === 'name' ? value : Number(value) || 0,
            } : item));
          };
          const removeItem = (index: number) => {
            setOcrItems(prev => prev.filter((_, i) => i !== index));
          };
          const addItem = () => {
            setOcrItems(prev => [...prev, { name: '', price: 0, qty: 1 }]);
          };
          const updateCharge = (index: number, field: 'name' | 'price', value: string) => {
            setOcrCharges(prev => prev.map((c, i) => i === index ? {
              ...c,
              [field]: field === 'name' ? value : Number(value) || 0,
            } : c));
          };
          const removeCharge = (index: number) => {
            setOcrCharges(prev => prev.filter((_, i) => i !== index));
          };
          const addCharge = (name: string, price: number, included: boolean) => {
            setOcrCharges(prev => [...prev, { name, price }]);
            if (included) setOcrChargesIncluded(true);
          };
          const toggleChargeIncluded = () => setOcrChargesIncluded(prev => !prev);
          const computedSubtotal = ocrItems.reduce((s, i) => s + i.price * i.qty, 0);
          const computedCharges = ocrChargesIncluded ? 0 : ocrCharges.reduce((s, c) => s + c.price, 0);
          const computedTotal = computedSubtotal + computedCharges;
          const hasMissingPrices = ocrItems.some(i => i.price === 0);

          return (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2"><CheckCircle className="text-green-400" size={24} /><h2 className="text-white text-xl font-bold">{ocrEditing ? 'Edit Receipt' : 'Receipt Scanned'}</h2></div>
              <button onClick={() => setOcrEditing(!ocrEditing)} className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all border ${ocrEditing ? 'bg-blue-500/20 border-blue-400/50 text-blue-300' : 'bg-white/5 border-white/15 text-white/50 hover:text-white'}`}>
                <Edit2 size={14} className="inline mr-1" />{ocrEditing ? 'Done' : 'Edit'}
              </button>
            </div>

            {!ocrEditing ? (
              <>
                <div className="bg-white/5 rounded-xl p-4 mb-6 max-h-96 overflow-y-auto">
                  <div className="space-y-2">
                    {ocrItems.map((item, index) => (
                      <div key={index} className={`flex justify-between items-start py-2 ${index < ocrItems.length - 1 || ocrCharges.length > 0 ? 'border-b border-white/10' : ''}`}>
                        <div className="flex-1"><div className={`text-sm ${item.price === 0 ? 'text-amber-300' : 'text-white'}`}>{item.name}</div>{item.qty > 1 && <div className="text-white/50 text-xs mono">Qty: {item.qty}</div>}</div>
                        {item.price === 0 ? (
                          <div className="text-amber-400 mono font-semibold flex items-center gap-1">$?.?? <span className="text-[10px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded">needs price</span></div>
                        ) : (
                          <div className="text-green-400 mono font-semibold">${(item.price * item.qty).toFixed(2)}</div>
                        )}
                      </div>
                    ))}
                    {ocrCharges.length > 0 && (
                      <div className="pt-2">
                        {ocrCharges.map((charge, index) => (
                          <div key={`charge-${index}`} className={`flex justify-between items-start py-2 ${index < ocrCharges.length - 1 ? 'border-b border-white/10' : ''}`}>
                            <div className="text-white/50 text-sm">{charge.name}</div>
                            <div className={`mono font-semibold ${ocrChargesIncluded ? 'text-white/30 line-through' : 'text-amber-400'}`}>
                              {ocrChargesIncluded ? '' : '+'} ${charge.price.toFixed(2)}
                            </div>
                          </div>
                        ))}
                        <div className={`text-xs mt-1 px-2 py-1 rounded ${ocrChargesIncluded ? 'text-white/40 bg-white/5' : 'text-amber-300/80 bg-amber-500/10'}`}>
                          {ocrChargesIncluded ? 'Already in menu prices — not added' : `+$${ocrCharges.reduce((s, c) => s + c.price, 0).toFixed(2)} added to total`}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="mt-4 pt-4 border-t-2 border-white/20 flex justify-between items-center"><span className="text-white font-bold">TOTAL</span><span className="text-green-400 text-xl mono font-bold">${computedTotal.toFixed(2)}</span></div>
                </div>
                {hasMissingPrices ? (
                  <div className="bg-amber-500/10 rounded-xl p-4 mb-6 border border-amber-400/30"><p className="text-amber-200 text-sm"><strong className="text-white">⚠ Some prices couldn't be read</strong><br />Tap <strong>Edit</strong> to fill in the missing prices before continuing.</p></div>
                ) : (
                  <div className="bg-blue-500/10 rounded-xl p-4 mb-6 border border-blue-400/30"><p className="text-blue-200 text-sm"><strong className="text-white">✓ Receipt extracted</strong><br />{ocrItems.length} food items{ocrCharges.length > 0 ? ` + ${ocrCharges.length} charges${ocrChargesIncluded ? ' (included in prices)' : ''}` : ''} = ${computedTotal.toFixed(2)}</p></div>
                )}
              </>
            ) : (
              <>
                <p className="text-blue-200 text-sm mb-5">Edit names, prices, or remove incorrect items</p>
                <div className="space-y-3 mb-4 max-h-[45vh] overflow-y-auto">
                  {ocrItems.map((item, index) => (
                    <div key={index} className={`bg-white/5 rounded-xl p-3 border ${item.price === 0 ? 'border-amber-400/50 bg-amber-500/5' : 'border-white/10'}`}>
                      <div className="flex items-start gap-2">
                        <div className="flex-1 space-y-2">
                          <input type="text" value={item.name} onChange={(e) => updateItem(index, 'name', e.target.value)} className="w-full bg-white/10 text-white text-sm rounded-lg px-3 py-2 border border-white/10 focus:border-blue-400/50 focus:outline-none" placeholder="Item name" />
                          <div className="flex gap-2">
                            <div className="flex-1">
                              <label className={`text-[10px] uppercase tracking-wider ${item.price === 0 ? 'text-amber-400' : 'text-white/40'}`}>{item.price === 0 ? 'Price ⚠' : 'Price'}</label>
                              <input type="number" step="0.01" value={item.price || ''} onChange={(e) => updateItem(index, 'price', e.target.value)} placeholder={item.price === 0 ? '?.??' : ''} className={`w-full bg-white/10 text-sm mono rounded-lg px-3 py-2 border focus:outline-none ${item.price === 0 ? 'border-amber-400/50 text-amber-400 focus:border-amber-400 placeholder:text-amber-400/40' : 'border-white/10 text-green-400 focus:border-blue-400/50'}`} />
                            </div>
                            <div className="w-16">
                              <label className="text-white/40 text-[10px] uppercase tracking-wider">Qty</label>
                              <input type="number" min="1" value={item.qty || ''} onChange={(e) => updateItem(index, 'qty', e.target.value)} className="w-full bg-white/10 text-white text-sm mono rounded-lg px-3 py-2 border border-white/10 focus:border-blue-400/50 focus:outline-none" />
                            </div>
                          </div>
                        </div>
                        <button onClick={() => removeItem(index)} className="text-red-400/60 hover:text-red-400 p-1 mt-1"><X size={18} /></button>
                      </div>
                    </div>
                  ))}
                </div>
                <button onClick={addItem} className="w-full mb-4 py-2.5 rounded-xl border-2 border-dashed border-white/20 text-white/50 hover:text-white hover:border-white/40 text-sm font-semibold transition-all">+ Add Item</button>
                <div className={`mb-4 rounded-xl p-4 border ${ocrCharges.length > 0 ? (ocrChargesIncluded ? 'bg-white/5 border-white/10' : 'bg-amber-500/5 border-amber-400/20') : 'bg-white/5 border-white/10'}`}>
                  <div className="text-white/40 text-xs font-semibold uppercase tracking-wider mb-3">GST & Service Charge</div>
                  {ocrCharges.length > 0 && (
                    <>
                      {ocrCharges.map((charge, index) => (
                        <div key={`charge-${index}`} className="flex items-center gap-2 mb-2">
                          <input type="text" value={charge.name} onChange={(e) => updateCharge(index, 'name', e.target.value)} className={`flex-1 bg-white/10 text-sm rounded-lg px-3 py-2 border border-white/10 focus:border-blue-400/50 focus:outline-none ${ocrChargesIncluded ? 'text-white/30' : 'text-white/60'}`} />
                          <input type="number" step="0.01" value={charge.price || ''} onChange={(e) => updateCharge(index, 'price', e.target.value)} className={`w-24 bg-white/10 text-sm mono rounded-lg px-3 py-2 border border-white/10 focus:border-blue-400/50 focus:outline-none ${ocrChargesIncluded ? 'text-white/30 line-through' : 'text-amber-400'}`} />
                          <button onClick={() => removeCharge(index)} className="text-red-400/60 hover:text-red-400 p-1"><X size={18} /></button>
                        </div>
                      ))}
                      <div className="flex rounded-lg overflow-hidden border border-white/15 mt-3 mb-1">
                        <button onClick={() => setOcrChargesIncluded(false)} className={`flex-1 py-2.5 text-xs font-semibold transition-all ${!ocrChargesIncluded ? 'bg-amber-500/25 text-amber-300 border-r border-amber-400/30' : 'bg-white/5 text-white/30 border-r border-white/10'}`}>
                          Add to total
                        </button>
                        <button onClick={() => setOcrChargesIncluded(true)} className={`flex-1 py-2.5 text-xs font-semibold transition-all ${ocrChargesIncluded ? 'bg-white/10 text-white/60' : 'bg-white/5 text-white/30'}`}>
                          Already in prices
                        </button>
                      </div>
                      <div className={`text-xs mt-1 ${ocrChargesIncluded ? 'text-white/30' : 'text-amber-300/70'}`}>
                        {ocrChargesIncluded ? 'Prices already include GST/svc — charges shown for info only' : `$${computedCharges.toFixed(2)} will be added on top and split proportionally`}
                      </div>
                    </>
                  )}
                  <div className={`flex gap-2 ${ocrCharges.length > 0 ? 'mt-3' : 'mt-0'}`}>
                    <button onClick={() => addCharge('9% GST', +(computedSubtotal * 0.09).toFixed(2), false)} className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white/5 border border-white/15 text-white/50 hover:text-white hover:border-white/30 transition-all">+ 9% GST</button>
                    <button onClick={() => addCharge('10% Svc Charge', +(computedSubtotal * 0.10).toFixed(2), false)} className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white/5 border border-white/15 text-white/50 hover:text-white hover:border-white/30 transition-all">+ 10% Svc</button>
                  </div>
                </div>
                <div className="bg-white/5 rounded-xl p-4 mb-6 border border-white/10">
                  <div className="flex justify-between items-center"><span className="text-white font-bold">TOTAL</span><span className="text-green-400 text-xl mono font-bold">${computedTotal.toFixed(2)}</span></div>
                  {!ocrChargesIncluded && computedCharges > 0 && <div className="text-white/40 text-xs mono mt-1 text-right">${computedSubtotal.toFixed(2)} + ${computedCharges.toFixed(2)} charges</div>}
                </div>
              </>
            )}

            <button onClick={() => {
              setOcrSubtotal(computedSubtotal);
              setBillAmount(computedTotal.toFixed(2));
              setStep('who-paid');
            }} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between">
              <span>Continue</span><ArrowRight size={20} />
            </button>
            <button onClick={() => setStep('ocr-scan')} className="w-full mt-3 btn-secondary text-white px-6 py-3 rounded-xl font-semibold">Rescan Receipt</button>
          </div>
          );
        })()}

        {/* Manual Bill */}
        {step === 'manual-bill' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">Enter Bill Amount</h2>
            <p className="text-blue-200 text-sm mb-5">Enter the total amount on your bill</p>

            <div className="mb-5">
              <div className="text-white/50 text-xs mb-1.5">Total Amount</div>
              <div className="relative">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-white/50 text-2xl mono">$</span>
                <input type="number" value={billAmount} onChange={(e) => setBillAmount(e.target.value)} placeholder="0.00" className="w-full bg-white/10 border border-white/20 rounded-xl px-12 py-4 text-white text-2xl mono placeholder-white/30 focus:outline-none focus:border-blue-400" step="0.01" />
              </div>
            </div>

            <button onClick={() => setStep('who-paid')} disabled={!billAmount || parseFloat(billAmount) <= 0} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed">
              <span>Continue</span><ArrowRight size={20} />
            </button>
            <button onClick={() => setStep('ocr-choice')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
        )}

        {/* Who Paid the Bill? */}
        {step === 'who-paid' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="text-center mb-6">
              <div className="w-16 h-16 mx-auto mb-3 rounded-2xl bg-gradient-to-br from-amber-400 to-orange-500 flex items-center justify-center"><DollarSign className="text-white" size={32} /></div>
              <h2 className="text-white text-xl font-bold mb-1">Who Paid the Bill?</h2>
              <p className="text-blue-200 text-sm">This person will receive everyone's payments</p>
            </div>
            <div className="space-y-3 mb-6">
              <button
                onClick={() => { setPayeeChoice('me'); if (isTMA && window.Telegram?.WebApp?.initDataUnsafe?.user?.first_name) setCurrentUser(window.Telegram.WebApp.initDataUnsafe.user.first_name); }}
                className={`w-full rounded-xl px-4 py-4 text-left transition-all border-2 ${
                  payeeChoice === 'me'
                    ? 'bg-amber-500/20 border-amber-400/60 text-white'
                    : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${payeeChoice === 'me' ? 'border-amber-400 bg-amber-400' : 'border-white/30'}`}>
                    {payeeChoice === 'me' && <div className="w-2 h-2 rounded-full bg-white"></div>}
                  </div>
                  <div>
                    <div className="font-semibold text-sm">I paid</div>
                    <div className="text-xs opacity-60">I'm collecting payments from others</div>
                  </div>
                </div>
              </button>
              {payeeChoice === 'me' && (
                <div className="bg-white/5 rounded-xl p-4 border border-white/10 space-y-3 animate-in">
                  <div>
                    <div className="text-white/50 text-xs mb-1">Your name</div>
                    {knownMembers.length > 0 ? (
                      <div className="space-y-2">
                        {knownMembers.map(m => (
                          <button
                            key={m.id}
                            onClick={() => { setCurrentUser(m.name); if (m.id) setMyTelegramId(m.id); }}
                            className={`w-full text-left px-3 py-2.5 rounded-lg text-sm mono transition-all border ${
                              currentUser === m.name
                                ? 'bg-amber-500/20 border-amber-400/50 text-white'
                                : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                            }`}
                          >
                            @{m.name}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <input
                        type="text"
                        value={currentUser === 'you' ? '' : currentUser}
                        onChange={(e) => setCurrentUser(e.target.value)}
                        placeholder="e.g. Chris"
                        className="w-full bg-white/10 border border-white/20 rounded-lg px-3 py-2.5 text-white mono text-sm placeholder-white/30 focus:outline-none focus:border-amber-400"
                      />
                    )}
                  </div>
                </div>
              )}
              <button
                onClick={() => setPayeeChoice('other')}
                className={`w-full rounded-xl px-4 py-4 text-left transition-all border-2 ${
                  payeeChoice === 'other'
                    ? 'bg-amber-500/20 border-amber-400/60 text-white'
                    : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${payeeChoice === 'other' ? 'border-amber-400 bg-amber-400' : 'border-white/30'}`}>
                    {payeeChoice === 'other' && <div className="w-2 h-2 rounded-full bg-white"></div>}
                  </div>
                  <div>
                    <div className="font-semibold text-sm">Someone else</div>
                    <div className="text-xs opacity-60">A friend paid, I'm helping organize</div>
                  </div>
                </div>
              </button>
              {payeeChoice === 'other' && (
                <div className="bg-white/5 rounded-xl p-4 border border-white/10 space-y-3 animate-in">
                  <div>
                    <div className="text-white/50 text-xs mb-1">Who paid?</div>
                    {knownMembers.length > 0 ? (
                      <div className="space-y-2">
                        {knownMembers.map(m => (
                          <button
                            key={m.id}
                            onClick={() => { setCurrentUser(m.name); if (m.id) setMyTelegramId(m.id); }}
                            className={`w-full text-left px-3 py-2.5 rounded-lg text-sm mono transition-all border ${
                              currentUser === m.name
                                ? 'bg-amber-500/20 border-amber-400/50 text-white'
                                : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                            }`}
                          >
                            @{m.name}
                          </button>
                        ))}
                      </div>
                    ) : (
                    <input
                      type="text"
                      value={currentUser === 'you' ? '' : currentUser}
                      onChange={(e) => setCurrentUser(e.target.value)}
                      placeholder="e.g. Tom"
                      className="w-full bg-white/10 border border-white/20 rounded-lg px-3 py-2.5 text-white mono text-sm placeholder-white/30 focus:outline-none focus:border-amber-400"
                    />
                    )}
                  </div>
                </div>
              )}
            </div>
            <div className="bg-white/5 rounded-xl px-4 py-3 border border-white/10 mb-4">
              <div className="text-white/50 text-xs mb-1">Event name</div>
              <input
                type="text"
                value={eventName}
                onChange={(e) => setEventName(e.target.value)}
                placeholder="e.g. dinner, birthday party"
                className="w-full bg-transparent text-white mono text-sm outline-none placeholder-white/30"
              />
            </div>
            <div className={`bg-white/5 rounded-xl px-4 py-3 border-2 mb-4 transition-all ${payeePhone.length === 8 && /^[89]/.test(payeePhone) ? 'border-green-400/50' : 'border-red-400/40'}`}>
              <div className="text-white/50 text-xs mb-1">PayNow phone number (for QR code)</div>
              <div className="flex items-center gap-2">
                <span className="text-white/50 mono text-sm">+65</span>
                <input
                  type="tel"
                  value={payeePhone}
                  onChange={(e) => {
                    let val = e.target.value.replace(/\D/g, '');
                    // Strip leading 65 if user types +65
                    if (val.startsWith('65') && val.length > 8) val = val.slice(2);
                    setPayeePhone(val.slice(0, 8));
                  }}
                  placeholder="9123 4567"
                  className="w-full bg-transparent text-white mono text-sm outline-none placeholder-white/30"
                  maxLength={8}
                />
              </div>
              {payeePhone.length === 0 && (
                <div className="text-red-400/70 text-[10px] mt-1">Required — participants will pay to this number</div>
              )}
              {payeePhone.length > 0 && payeePhone.length < 8 && (
                <div className="text-red-400 text-[10px] mt-1">Enter 8 digits</div>
              )}
              {payeePhone.length === 8 && !/^[89]/.test(payeePhone) && (
                <div className="text-red-400 text-[10px] mt-1">SG mobile numbers start with 8 or 9</div>
              )}
              {payeePhone.length === 8 && /^[89]/.test(payeePhone) && (
                <div className="text-green-400 text-[10px] mt-1">+65 {payeePhone.slice(0,4)} {payeePhone.slice(4)}</div>
              )}
            </div>
            <div className="bg-amber-500/10 rounded-xl p-4 border border-amber-400/20 mb-6">
              <div className="text-amber-300 text-xs font-semibold mb-1">Please confirm your details</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-white/60">Payee:</span><span className="text-white mono">@{currentUser}</span></div>
                <div className="flex justify-between"><span className="text-white/60">PayNow:</span><span className="text-white mono">{payeePhone.length === 8 ? `+65 ${payeePhone.slice(0,4)} ${payeePhone.slice(4)}` : '—'}</span></div>
                <div className="flex justify-between"><span className="text-white/60">Event:</span><span className="text-white">{eventName}</span></div>
              </div>
            </div>
            <button
              onClick={() => { try { localStorage.setItem('grouppay_phone', payeePhone); } catch {} setStep('participants'); }}
              disabled={!currentUser || currentUser === 'you' || payeePhone.length !== 8 || !/^[89]/.test(payeePhone)}
              className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>Confirm & Continue</span><ArrowRight size={20} />
            </button>
            <button onClick={() => setStep(ocrItems.length > 0 ? 'ocr-result' : 'manual-bill')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
        )}

        {/* Participants */}
        {step === 'participants' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">Add Participants</h2>
            <p className="text-blue-200 text-sm mb-2">Who owes money to @{currentUser}?</p>
            <div className="bg-amber-500/10 rounded-lg px-3 py-2 mb-6 border border-amber-400/20">
              <p className="text-amber-200 text-xs">Payments will go to <strong className="text-amber-300 mono">@{currentUser}</strong> for <strong className="text-white">{eventName}</strong></p>
            </div>

            {knownMembers.length > 0 && (
              <div className="space-y-2 mb-4">
                <p className="text-white/60 text-xs uppercase tracking-wider font-semibold mb-2">Group Members</p>
                <div className="bg-blue-500/10 rounded-lg px-3 py-2 mb-3 border border-blue-400/20">
                  <p className="text-blue-200 text-xs">The bot can only see members who have <strong className="text-blue-300">messaged</strong> or <strong className="text-blue-300">joined</strong> the group after it was added.</p>
                </div>
                {(() => {
                  const selectableMembers = knownMembers.filter(m => !isPayerParticipant(m.name));
                  const allSelected = selectableMembers.length > 0 && selectableMembers.every(m => participants.includes(m.name));
                  return (
                    <button
                      onClick={() => {
                        if (allSelected) {
                          setParticipants(participants.filter(p => !selectableMembers.some(m => m.name === p)));
                        } else {
                          const newNames = selectableMembers.map(m => m.name).filter(n => !participants.includes(n));
                          setParticipants([...participants.filter(p => p.trim()), ...newNames]);
                        }
                      }}
                      className={`w-full mb-3 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all border ${
                        allSelected
                          ? 'bg-blue-500/20 border-blue-400/50 text-blue-300'
                          : 'bg-white/5 border-white/15 text-white/50 hover:border-white/30'
                      }`}
                    >
                      {allSelected ? 'Deselect All' : 'Select All'}
                    </button>
                  );
                })()}
                {knownMembers.filter(m => !isPayerParticipant(m.name)).map((member) => {
                  const isSelected = participants.includes(member.name);
                  return (
                    <label key={member.id || member.name} className="flex items-center gap-3 bg-white/5 hover:bg-white/10 rounded-xl px-4 py-3 cursor-pointer transition border border-white/10">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {
                          if (isSelected) {
                            setParticipants(participants.filter(p => p !== member.name));
                          } else {
                            const firstEmpty = participants.findIndex(p => !p.trim());
                            if (firstEmpty >= 0) {
                              const next = [...participants];
                              next.splice(firstEmpty, 0, member.name);
                              setParticipants(next);
                            } else {
                              setParticipants([...participants, member.name]);
                            }
                          }
                        }}
                        className="w-5 h-5 rounded border-white/30 accent-blue-500"
                      />
                      <span className="text-white text-sm font-medium">{member.name}</span>
                    </label>
                  );
                })}
              </div>
            )}

            <div className="space-y-3 mb-6">
              {participants.map((participant, index) => {
                if (knownMembers.length > 0 && knownMembers.some(m => m.name === participant)) return null;
                return (
                  <div key={index} className="flex gap-2">
                    <div className="flex-1 relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-white/50">@</span>
                      <input type="text" value={participant} onChange={(e) => updateParticipant(index, e.target.value)} placeholder="telehandle" className="w-full bg-white/10 border border-white/20 rounded-xl pl-8 pr-4 py-3 text-white mono text-sm placeholder-white/30 focus:outline-none focus:border-blue-400" />
                    </div>
                    <button onClick={() => removeParticipant(index)} className="px-3 py-3 bg-red-500/20 hover:bg-red-500/30 rounded-xl text-red-300 transition"><XCircle size={20} /></button>
                  </div>
                );
              })}
            </div>

            <button onClick={addParticipant} className="w-full btn-secondary text-white px-6 py-3 rounded-xl font-semibold mb-4">+ Add Another Person</button>
            <button onClick={() => setStep(ocrItems.length > 0 ? 'item-assign' : 'split-type')} disabled={participants.filter(p => p.trim()).length === 0} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed"><span>{ocrItems.length > 0 ? 'Assign Items' : 'Continue'}</span><ArrowRight size={20} /></button>
            <button onClick={() => setStep('who-paid')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
        )}

        {/* Item Assignment (receipt scan only) */}
        {step === 'item-assign' && (() => {
          const allPeople = [currentUser, ...otherParticipants];
          const totalCharges = ocrChargesIncluded ? 0 : ocrCharges.reduce((s, c) => s + c.price, 0);
          const expandedOcrItems = ocrItems.flatMap((item) => {
            const qty = Math.max(1, Math.floor(Number(item.qty) || 1));
            return Array.from({ length: qty }, (_, unitIdx) => ({
              name: qty > 1 ? `${item.name} (${unitIdx + 1}/${qty})` : item.name,
              price: item.price,
            }));
          });
          const assignedCount = expandedOcrItems.filter((_, idx) => (itemAssignments[idx] || []).length > 0).length;
          const allAssigned = expandedOcrItems.length > 0 && assignedCount === expandedOcrItems.length;

          // Calculate per-person totals
          const personFoodTotals: Record<string, number> = {};
          allPeople.forEach(p => { personFoodTotals[p] = 0; });
          expandedOcrItems.forEach((item, idx) => {
            const assignees = itemAssignments[idx] || [];
            if (assignees.length === 0) return;
            const perPerson = item.price / assignees.length;
            assignees.forEach(name => {
              personFoodTotals[name] = (personFoodTotals[name] || 0) + perPerson;
            });
          });

          const personFinalTotals: Record<string, number> = {};
          allPeople.forEach(name => {
            const ratio = ocrSubtotal > 0 ? personFoodTotals[name] / ocrSubtotal : 0;
            personFinalTotals[name] = personFoodTotals[name] + ratio * totalCharges;
          });

          const toggleAssignment = (itemIdx: number, person: string) => {
            setItemAssignments(prev => {
              const current = prev[itemIdx] || [];
              const next = current.includes(person)
                ? current.filter(p => p !== person)
                : [...current, person];
              return { ...prev, [itemIdx]: next };
            });
          };

          const assignAllToItem = (itemIdx: number) => {
            setItemAssignments(prev => ({ ...prev, [itemIdx]: [...allPeople] }));
          };

          const handleContinue = () => {
            const amounts: Record<string, string> = {};
            // Payee's share
            amounts[currentUser] = personFinalTotals[currentUser].toFixed(2);
            // Other participants
            otherParticipants.forEach(name => {
              amounts[name] = personFinalTotals[name].toFixed(2);
            });
            setCustomAmounts(amounts);
            setEvenSplit(false);
            setBillAmount(Object.values(personFinalTotals).reduce((s, v) => s + v, 0).toFixed(2));
            setStep('review-split');
          };

          return (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-1">Assign Items</h2>
            <p className="text-blue-200 text-sm mb-4">Tap people to assign each item. Shared items split evenly.</p>

            <div className="text-white/40 text-xs mb-2 font-semibold uppercase tracking-wider">{assignedCount}/{expandedOcrItems.length} items assigned</div>
            <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden mb-3">
              <div className={`h-full rounded-full transition-all duration-300 ${allAssigned ? 'bg-green-500' : 'bg-blue-500'}`} style={{ width: `${expandedOcrItems.length > 0 ? (assignedCount / expandedOcrItems.length) * 100 : 0}%` }} />
            </div>

            <button
              onClick={() => {
                const all: Record<number, string[]> = {};
                expandedOcrItems.forEach((_, idx) => { all[idx] = [...allPeople]; });
                setItemAssignments(all);
              }}
              className="w-full mb-5 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all border bg-white/5 border-white/15 text-white/50 hover:border-blue-400/50 hover:text-blue-300"
            >
              Split Everything Evenly
            </button>

            <div className="space-y-4 mb-6 max-h-[50vh] overflow-y-auto">
              {expandedOcrItems.map((item, idx) => {
                const assignees = itemAssignments[idx] || [];
                const isAssigned = assignees.length > 0;
                return (
                  <div key={idx} className={`rounded-xl p-4 border transition-all ${isAssigned ? 'bg-green-500/5 border-green-400/30' : 'bg-white/5 border-white/10'}`}>
                    <div className="flex justify-between items-start mb-3">
                      <div className="flex-1">
                        <div className="text-white text-sm font-medium">{item.name}</div>
                      </div>
                      <div className="text-green-400 mono font-semibold text-sm">${item.price.toFixed(2)}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {allPeople.map(person => {
                        const isSelected = assignees.includes(person);
                        return (
                          <button
                            key={person}
                            onClick={() => toggleAssignment(idx, person)}
                            className={`px-3 py-1.5 rounded-full text-xs font-semibold transition-all border ${
                              isSelected
                                ? 'bg-blue-500/30 border-blue-400/60 text-blue-200'
                                : 'bg-white/5 border-white/15 text-white/40 hover:border-white/30'
                            }`}
                          >
                            {person}{isSelected && assignees.length > 1 ? ` · $${(item.price / assignees.length).toFixed(2)}` : ''}
                          </button>
                        );
                      })}
                      <button
                        onClick={() => assignAllToItem(idx)}
                        className="px-3 py-1.5 rounded-full text-xs font-semibold bg-white/5 border border-white/15 text-white/30 hover:text-white/60 hover:border-white/30 transition-all"
                      >
                        All
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Per-person breakdown */}
            <div className="bg-white/5 rounded-xl p-4 mb-6 border border-white/10">
              <div className="text-white/50 text-xs font-semibold uppercase tracking-wider mb-3">Per Person Breakdown</div>
              <div className="space-y-2">
                {allPeople.map(person => {
                  const food = personFoodTotals[person];
                  const final_ = personFinalTotals[person];
                  const charges = final_ - food;
                  return (
                    <div key={person} className="flex justify-between items-center py-1.5 border-b border-white/5 last:border-0">
                      <div>
                        <div className="text-white text-sm font-medium">{person}{person === currentUser ? ' (payee)' : ''}</div>
                        {charges > 0.005 && <div className="text-white/40 text-[10px] mono">${food.toFixed(2)} + ${charges.toFixed(2)} charges</div>}
                      </div>
                      <div className={`mono font-bold text-sm ${final_ > 0 ? 'text-green-400' : 'text-white/30'}`}>
                        ${final_.toFixed(2)}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 pt-3 border-t-2 border-white/20 flex justify-between items-center">
                <span className="text-white font-bold text-sm">TOTAL</span>
                <span className="text-green-400 mono font-bold">${Object.values(personFinalTotals).reduce((s, v) => s + v, 0).toFixed(2)}</span>
              </div>
            </div>

            <button
              onClick={handleContinue}
              disabled={!allAssigned}
              className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>Confirm Split</span><ArrowRight size={20} />
            </button>
            <button onClick={() => setStep('participants')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
          );
        })()}

        {/* Split Type */}
        {step === 'split-type' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">How should we split?</h2>
            <p className="text-blue-200 text-sm mb-6">Total: <span className="mono font-bold text-white">${finalBillStr}</span></p>
            <div className="space-y-3">
              <button onClick={() => { setEvenSplit(true); setStep('review-split'); }} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"><div className="flex items-center gap-3"><Users size={20} /><span>Split Evenly</span></div><ArrowRight size={20} /></button>

              <div className="border-t border-white/10 pt-3 mt-3">
                <p className="text-white/50 text-xs mb-3 uppercase tracking-wider font-semibold">Custom Split Method</p>
                <div className="flex gap-1 bg-white/5 rounded-xl p-1 mb-3 border border-white/10">
                  {[
                    { key: 'amount' as const, label: '$ Amount', icon: '$' },
                    { key: 'percentage' as const, label: '% Percent', icon: '%' },
                    { key: 'shares' as const, label: '# Shares', icon: '#' },
                  ].map(m => (
                    <button
                      key={m.key}
                      onClick={() => setSplitMethod(m.key)}
                      className={`flex-1 rounded-lg px-2 py-2.5 text-xs font-semibold transition-all ${
                        splitMethod === m.key
                          ? 'bg-blue-500 text-white shadow-lg'
                          : 'text-white/50 hover:text-white hover:bg-white/5'
                      }`}
                    >
                      <div>{m.icon}</div>
                      <div className="mt-0.5">{m.label}</div>
                    </button>
                  ))}
                </div>
                <button onClick={() => { setEvenSplit(false); setStep('custom-split'); }} className="w-full btn-secondary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"><div className="flex items-center gap-3"><Edit2 size={20} /><span>Custom Split ({splitMethod === 'amount' ? 'by Amount' : splitMethod === 'percentage' ? 'by %' : 'by Shares'})</span></div><ArrowRight size={20} /></button>
              </div>
            </div>
            <button onClick={() => setStep('participants')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
        )}

        {/* Custom Split */}
        {step === 'custom-split' && (() => {
          const total = finalBillAmount;
          const tallyTarget = useMenuPriceMode ? impliedSubtotal : finalBillAmount;
          const allKeys = [currentUser, ...otherParticipants];

          let assigned = 0;
          let remaining = 0;
          let pct = 0;
          let isValid = false;

          if (splitMethod === 'amount') {
            if (useMenuPriceMode) {
              assigned = allKeys.reduce((sum, k) => sum + getItemLinesSum(k), 0);
            } else {
              assigned = allKeys.reduce((sum, k) => sum + (parseFloat(customAmounts[k] || '') || 0), 0);
            }
            remaining = tallyTarget - assigned;
            pct = tallyTarget > 0 ? Math.min((assigned / tallyTarget) * 100, 100) : 0;
            isValid = Math.abs(remaining) < 0.01;
          } else if (splitMethod === 'percentage') {
            const totalPct = allKeys.reduce((sum, k) => sum + (parseFloat(customPercentages[k] || '') || 0), 0);
            assigned = totalPct;
            remaining = 100 - totalPct;
            pct = Math.min(totalPct, 100);
            isValid = Math.abs(remaining) < 0.01;
          } else {
            const totalShares = allKeys.reduce((sum, k) => sum + (parseFloat(customShares[k] || '') || 0), 0);
            assigned = totalShares;
            isValid = totalShares > 0;
            pct = 100;
            remaining = 0;
          }

          const tallyColor = isValid ? 'bg-green-500' : (splitMethod === 'amount' && assigned > tallyTarget) || (splitMethod === 'percentage' && assigned > 100) ? 'bg-red-500' : 'bg-amber-500';
          const tallyTextColor = isValid ? 'text-green-400' : (splitMethod === 'amount' && assigned > tallyTarget) || (splitMethod === 'percentage' && assigned > 100) ? 'text-red-400' : 'text-amber-400';

          const unfilledCount = allKeys.filter(k => {
            if (splitMethod === 'amount') {
              if (useMenuPriceMode) return getItemLinesSum(k) === 0;
              return !customAmounts[k] || parseFloat(customAmounts[k]) === 0;
            }
            if (splitMethod === 'percentage') return !customPercentages[k] || parseFloat(customPercentages[k]) === 0;
            return !customShares[k] || parseFloat(customShares[k]) === 0;
          }).length;

          return (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">
              {splitMethod === 'amount' ? (useMenuPriceMode ? 'Enter Menu Prices' : 'Enter Custom Amounts') : splitMethod === 'percentage' ? 'Enter Percentages' : 'Enter Shares'}
            </h2>
            <p className="text-blue-200 text-sm mb-3">
              {useMenuPriceMode ? (
                <>Menu subtotal: <span className="mono font-bold text-white">${impliedSubtotal.toFixed(2)}</span> <span className="text-white/40">→ Total: ${total.toFixed(2)}</span></>
              ) : (
                <>Total: <span className="mono font-bold text-white">${total.toFixed(2)}</span></>
              )}
            </p>

            <div className="flex gap-1 bg-white/5 rounded-xl p-1 mb-4 border border-white/10">
              {[
                { key: 'amount' as const, label: '$ Amt' },
                { key: 'percentage' as const, label: '% Pct' },
                { key: 'shares' as const, label: '# Share' },
              ].map(m => (
                <button
                  key={m.key}
                  onClick={() => setSplitMethod(m.key)}
                  className={`flex-1 rounded-lg px-2 py-2 text-xs font-semibold transition-all ${
                    splitMethod === m.key
                      ? 'bg-blue-500 text-white shadow-lg'
                      : 'text-white/50 hover:text-white hover:bg-white/5'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>

            {splitMethod === 'amount' && (
              <div className="mb-4 space-y-3 animate-in">
                <button
                  onClick={() => setMenuPriceMode(!menuPriceMode)}
                  className={`w-full flex items-center justify-between rounded-xl px-4 py-3 text-sm font-semibold transition-all border ${
                    menuPriceMode
                      ? 'bg-amber-500/15 border-amber-400/30 text-amber-300'
                      : 'bg-white/5 border-white/10 text-white/50'
                  }`}
                >
                  <span>{menuPriceMode ? 'Menu price mode (auto ++)' : 'Enter final amounts'}</span>
                  <span className="text-xs mono">{menuPriceMode ? 'ON' : 'OFF'}</span>
                </button>

                {menuPriceMode && (
                  <div className="space-y-2 animate-in">
                    <div className="flex gap-2">
                      <button
                        onClick={() => { setServiceChargeOn(true); setGstOn(true); setServiceChargeRate(10); setGstRate(9); }}
                        className={`flex-1 rounded-xl px-3 py-2.5 text-xs font-semibold transition-all border ${
                          serviceChargeOn && gstOn && serviceChargeRate === 10 && gstRate === 9
                            ? 'bg-green-500/20 border-green-400/50 text-green-300'
                            : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                        }`}
                      >
                        SG Standard<br /><span className="opacity-70">10% SC + 9% GST</span>
                      </button>
                      <button
                        onClick={() => { setServiceChargeOn(true); setGstOn(false); setServiceChargeRate(10); }}
                        className={`flex-1 rounded-xl px-3 py-2.5 text-xs font-semibold transition-all border ${
                          serviceChargeOn && !gstOn
                            ? 'bg-blue-500/20 border-blue-400/50 text-blue-300'
                            : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                        }`}
                      >
                        SC only<br /><span className="opacity-70">10% Service Charge</span>
                      </button>
                      <button
                        onClick={() => { setServiceChargeOn(false); setGstOn(true); setGstRate(9); }}
                        className={`flex-1 rounded-xl px-3 py-2.5 text-xs font-semibold transition-all border ${
                          !serviceChargeOn && gstOn
                            ? 'bg-purple-500/20 border-purple-400/50 text-purple-300'
                            : 'bg-white/5 border-white/10 text-white/60 hover:border-white/30'
                        }`}
                      >
                        GST only<br /><span className="opacity-70">9% GST</span>
                      </button>
                    </div>

                    <div className="bg-white/5 rounded-xl p-3 border border-white/10 flex items-center gap-3">
                      <div className="flex items-center gap-2 flex-1">
                        <span className="text-white/60 text-xs">SC</span>
                        <button
                          onClick={() => setServiceChargeOn(!serviceChargeOn)}
                          className={`w-9 h-5 rounded-full transition-all relative ${serviceChargeOn ? 'bg-blue-500' : 'bg-white/20'}`}
                        >
                          <div className={`w-4 h-4 rounded-full bg-white shadow-md absolute top-0.5 transition-all ${serviceChargeOn ? 'left-[18px]' : 'left-0.5'}`} />
                        </button>
                        {serviceChargeOn && <span className="text-white/40 text-xs mono">{serviceChargeRate}%</span>}
                      </div>
                      <div className="w-px h-4 bg-white/10" />
                      <div className="flex items-center gap-2 flex-1">
                        <span className="text-white/60 text-xs">GST</span>
                        <button
                          onClick={() => setGstOn(!gstOn)}
                          className={`w-9 h-5 rounded-full transition-all relative ${gstOn ? 'bg-blue-500' : 'bg-white/20'}`}
                        >
                          <div className={`w-4 h-4 rounded-full bg-white shadow-md absolute top-0.5 transition-all ${gstOn ? 'left-[18px]' : 'left-0.5'}`} />
                        </button>
                        {gstOn && <span className="text-white/40 text-xs mono">{gstRate}%</span>}
                      </div>
                      {hasCharges && <span className="text-amber-300 text-xs mono font-semibold">×{ppMultiplier.toFixed(4)}</span>}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="bg-white/5 rounded-xl p-4 mb-5 border border-white/10">
              <div className="flex justify-between items-center mb-2">
                <span className="text-white/70 text-sm">{splitMethod === 'shares' ? 'Total Shares' : 'Assigned'}</span>
                <span className={`mono text-sm font-bold ${tallyTextColor}`}>
                  {splitMethod === 'amount' ? `$${assigned.toFixed(2)} / $${tallyTarget.toFixed(2)}${useMenuPriceMode ? ' (menu)' : ''}` :
                   splitMethod === 'percentage' ? `${assigned.toFixed(1)}% / 100%` :
                   `${assigned} shares`}
                </span>
              </div>
              <div className="w-full h-2.5 bg-white/10 rounded-full overflow-hidden mb-2">
                <div className={`h-full rounded-full transition-all duration-300 ${tallyColor}`} style={{ width: `${pct}%` }} />
              </div>
              <div className="flex justify-between items-center">
                <span className={`text-xs font-semibold ${tallyTextColor}`}>
                  {isValid ? 'Perfectly balanced' :
                   splitMethod === 'amount' ? (assigned > tallyTarget ? `Over by $${(assigned - tallyTarget).toFixed(2)}` : `$${remaining.toFixed(2)} remaining`) :
                   splitMethod === 'percentage' ? (assigned > 100 ? `Over by ${(assigned - 100).toFixed(1)}%` : `${remaining.toFixed(1)}% remaining`) :
                   'Enter shares for each person'}
                </span>
                {splitMethod === 'amount' && remaining > 0.01 && unfilledCount > 0 && (
                  <button onClick={() => {
                    const perPerson = remaining / unfilledCount;
                    if (useMenuPriceMode) {
                      const updated = { ...customItemLines };
                      allKeys.forEach(k => {
                        if (getItemLinesSum(k) === 0) updated[k] = [perPerson.toFixed(2)];
                      });
                      setCustomItemLines(updated);
                    } else {
                      const updated = { ...customAmounts };
                      allKeys.forEach(k => {
                        if (!updated[k] || parseFloat(updated[k]) === 0) updated[k] = perPerson.toFixed(2);
                      });
                      setCustomAmounts(updated);
                    }
                  }} className="text-xs text-blue-300 hover:text-blue-200 underline transition">Split remaining evenly</button>
                )}
                {splitMethod === 'percentage' && remaining > 0.1 && unfilledCount > 0 && (
                  <button onClick={() => {
                    const perPerson = remaining / unfilledCount;
                    const updated = { ...customPercentages };
                    allKeys.forEach(k => {
                      if (!updated[k] || parseFloat(updated[k]) === 0) updated[k] = perPerson.toFixed(1);
                    });
                    setCustomPercentages(updated);
                  }} className="text-xs text-blue-300 hover:text-blue-200 underline transition">Split remaining evenly</button>
                )}
              </div>
            </div>

            <div className="space-y-3 mb-6">
              {allKeys.map((person) => {
                const isPayee = person === currentUser;
                const resolvedAmt = splitMethod === 'percentage' ? getAmountFromPercentage(person) : splitMethod === 'shares' ? getAmountFromShares(person) : (customAmounts[person] || '0.00');
                const personLines = customItemLines[person] || [''];
                const personMenuSum = getItemLinesSum(person);
                const personFinal = (personMenuSum * ppMultiplier).toFixed(2);
                return (
                  <div key={person} className={`rounded-xl p-4 ${isPayee ? 'bg-blue-500/10 border border-blue-400/20' : 'bg-white/5'}`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-white mono text-sm">@{person} {isPayee && <span className="text-blue-300 text-xs">(Payee)</span>}</span>
                      {useMenuPriceMode ? (
                        <span className="text-green-400 mono text-xs font-semibold">
                          {personMenuSum > 0 ? `$${personMenuSum.toFixed(2)} → $${personFinal}` : ''}
                        </span>
                      ) : splitMethod !== 'amount' ? (
                        <span className="text-green-400 mono text-xs font-semibold">= ${resolvedAmt}</span>
                      ) : null}
                    </div>

                    {useMenuPriceMode ? (
                      <div className="space-y-1.5">
                        {personLines.map((lineVal, idx) => (
                          <div key={idx} className="flex items-center gap-1.5">
                            <div className="relative flex-1">
                              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-white/50 mono">$</span>
                              <input
                                type="number"
                                value={lineVal}
                                onChange={(e) => {
                                  const updated = [...personLines];
                                  updated[idx] = e.target.value;
                                  setCustomItemLines({ ...customItemLines, [person]: updated });
                                }}
                                placeholder="0.00"
                                className="w-full bg-white/10 border border-white/20 rounded-lg pl-8 pr-4 py-2 text-white mono placeholder-white/30 focus:outline-none focus:border-blue-400 text-sm"
                                step="0.01"
                              />
                            </div>
                            {personLines.length > 1 && (
                              <button
                                onClick={() => {
                                  const updated = personLines.filter((_, i) => i !== idx);
                                  setCustomItemLines({ ...customItemLines, [person]: updated.length ? updated : [''] });
                                }}
                                className="text-white/30 hover:text-red-400 transition p-1"
                              >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                              </button>
                            )}
                          </div>
                        ))}
                        <button
                          onClick={() => {
                            setCustomItemLines({ ...customItemLines, [person]: [...personLines, ''] });
                          }}
                          className="text-xs text-blue-300 hover:text-blue-200 transition flex items-center gap-1 mt-1"
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                          Add item
                        </button>
                      </div>
                    ) : (
                      <div className="relative">
                        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-white/50 mono">
                          {splitMethod === 'amount' ? '$' : splitMethod === 'percentage' ? '%' : '#'}
                        </span>
                        <input
                          type="number"
                          value={splitMethod === 'amount' ? (customAmounts[person] || '') : splitMethod === 'percentage' ? (customPercentages[person] || '') : (customShares[person] || '')}
                          onChange={(e) => {
                            if (splitMethod === 'amount') setCustomAmounts({ ...customAmounts, [person]: e.target.value });
                            else if (splitMethod === 'percentage') setCustomPercentages({ ...customPercentages, [person]: e.target.value });
                            else setCustomShares({ ...customShares, [person]: e.target.value });
                          }}
                          placeholder={splitMethod === 'amount' ? '0.00' : splitMethod === 'percentage' ? '0' : '1'}
                          className="w-full bg-white/10 border border-white/20 rounded-lg pl-8 pr-4 py-2 text-white mono placeholder-white/30 focus:outline-none focus:border-blue-400"
                          step={splitMethod === 'amount' ? '0.01' : '1'}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <button onClick={() => setStep('review-split')} disabled={!isValid} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between disabled:opacity-50 disabled:cursor-not-allowed"><span>{isValid ? 'Review Split' : splitMethod === 'amount' ? `$${remaining.toFixed(2)} remaining` : splitMethod === 'percentage' ? `${remaining.toFixed(1)}% remaining` : 'Enter shares'}</span><ArrowRight size={20} /></button>
            <button onClick={() => setStep('split-type')} className="w-full mt-4 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
          </div>
          );
        })()}

        {/* Review Split Screen */}
        {step === 'review-split' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="flex items-center gap-3 mb-2">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-green-400 to-emerald-500 flex items-center justify-center">
                <CheckCircle className="text-white" size={22} />
              </div>
              <div>
                <h2 className="text-white text-xl font-bold">Review Split</h2>
                <p className="text-blue-200 text-xs">Confirm before sending to group</p>
              </div>
            </div>

            <div className="flex gap-1 bg-white/5 rounded-xl p-1 my-4 border border-white/10">
              <button onClick={() => setReviewTab('overview')} className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all ${reviewTab === 'overview' ? 'bg-blue-500 text-white shadow-lg' : 'text-white/50 hover:text-white'}`}>
                Overview
              </button>
              <button onClick={() => setReviewTab('details')} className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all ${reviewTab === 'details' ? 'bg-blue-500 text-white shadow-lg' : 'text-white/50 hover:text-white'}`}>
                Split Details
              </button>
            </div>

            {reviewTab === 'overview' && (
              <div className="bg-white/5 rounded-xl p-4 mb-5">
                {useMenuPriceMode ? (
                  <div className="mb-3 pb-3 border-b border-white/10 space-y-1.5">
                    <div className="flex justify-between"><span className="text-white/50 text-sm">Menu Subtotal</span><span className="text-white mono text-sm">${impliedSubtotal.toFixed(2)}</span></div>
                    {serviceChargeOn && <div className="flex justify-between"><span className="text-white/50 text-sm">Service ({serviceChargeRate}%)</span><span className="text-amber-300 mono text-sm">+${impliedSC.toFixed(2)}</span></div>}
                    {gstOn && <div className="flex justify-between"><span className="text-white/50 text-sm">GST ({gstRate}%)</span><span className="text-amber-300 mono text-sm">+${impliedGST.toFixed(2)}</span></div>}
                    <div className="flex justify-between items-center pt-1.5 border-t border-white/10"><span className="text-white font-bold">Total</span><span className="text-green-400 text-xl mono font-bold">${finalBillStr}</span></div>
                  </div>
                ) : (
                  <div className="flex justify-between items-center mb-3 pb-3 border-b border-white/10"><span className="text-white/70">Total Bill</span><span className="text-white text-xl mono font-bold">${finalBillStr}</span></div>
                )}

                <div className="text-white/40 text-xs font-semibold uppercase tracking-wider mb-2">
                  {evenSplit ? 'Even Split' : `Custom Split (by ${splitMethod})`} · {otherParticipants.length + 1} people
                </div>

                <div className="grid grid-cols-3 gap-2 mb-3">
                  <div className="bg-white/5 rounded-lg p-2 text-center"><div className="text-green-400 mono font-bold text-sm">${finalBillStr}</div><div className="text-white/40 text-[10px]">Total</div></div>
                  <div className="bg-white/5 rounded-lg p-2 text-center"><div className="text-blue-400 font-bold text-sm">{otherParticipants.length + 1}</div><div className="text-white/40 text-[10px]">People</div></div>
                  <div className="bg-white/5 rounded-lg p-2 text-center"><div className="text-purple-400 mono font-bold text-sm">${evenSplit ? calculateSplit() : '~'}</div><div className="text-white/40 text-[10px]">Avg/Person</div></div>
                </div>

                <div className="flex justify-between items-center bg-blue-500/10 rounded-lg px-3 py-2.5 border border-blue-400/20 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-white mono text-sm">@{currentUser}</span>
                    <span className="text-blue-300 text-[10px] bg-blue-500/20 px-1.5 py-0.5 rounded">Payee</span>
                  </div>
                  <span className="text-blue-400 mono font-semibold">${getResolvedAmount(currentUser)}</span>
                </div>
                {otherParticipants.map((participant) => (
                  <div key={participant} className="flex justify-between items-center bg-white/5 rounded-lg px-3 py-2.5 mb-1">
                    <span className="text-white mono text-sm">@{participant}</span>
                    <span className="text-green-400 mono font-semibold">${getResolvedAmount(participant)}</span>
                  </div>
                ))}
              </div>
            )}

            {reviewTab === 'details' && (
              <div className="bg-white/5 rounded-xl p-4 mb-5">
                <div className="text-white/40 text-xs font-semibold uppercase tracking-wider mb-3">
                  Split by {splitMethod === 'shares' ? 'Shares' : splitMethod === 'percentage' ? 'Percentage' : 'Amount'}
                </div>
                {[currentUser, ...otherParticipants].map((person) => {
                  const amt = parseFloat(getResolvedAmount(person));
                  const pctOfTotal = finalBillAmount > 0 ? (amt / finalBillAmount) * 100 : 0;
                  const shareCount = splitMethod === 'shares' ? (customShares[person] || (evenSplit ? '1' : '0')) : null;
                  return (
                    <div key={person} className="mb-3 last:mb-0">
                      <div className="flex justify-between items-center mb-1">
                        <div className="flex items-center gap-2">
                          <span className="text-white mono text-sm">@{person}</span>
                          {person === currentUser && <span className="text-blue-300 text-[10px] bg-blue-500/20 px-1.5 py-0.5 rounded">Payee</span>}
                        </div>
                        <div className="flex items-center gap-2">
                          {shareCount && <span className="text-white/40 text-xs">{shareCount} shares</span>}
                          <span className="text-green-400 mono text-sm font-semibold">${getResolvedAmount(person)}</span>
                        </div>
                      </div>
                      <div className="w-full h-2 bg-white/10 rounded-full overflow-hidden">
                        <div className="h-full rounded-full bg-gradient-to-r from-blue-400 to-cyan-400 transition-all" style={{ width: `${pctOfTotal}%` }} />
                      </div>
                      <div className="text-right text-white/30 text-[10px] mt-0.5">{pctOfTotal.toFixed(1)}%</div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="space-y-3">
              <button
                onClick={handleConfirmSplit}
                className="w-full bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-400 hover:to-blue-500 text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-center gap-3 transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-blue-500/30 group"
              >
                <span>Send to Group</span>
                <ArrowRight size={18} className="group-hover:translate-x-1 transition-transform" />
              </button>
              <button onClick={() => setStep(evenSplit ? 'split-type' : 'custom-split')} className="w-full btn-secondary text-white px-6 py-3 rounded-xl font-semibold">Edit Split</button>
            </div>
          </div>
        )}

        {/* Post-Send: Success + Reminder Setup */}
        {step === 'auto-remind-setup' && (() => {
          return (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="text-center mb-6">
              <div className="w-20 h-20 mx-auto mb-4 rounded-full bg-gradient-to-br from-green-500 to-emerald-600 flex items-center justify-center animate-in">
                <CheckCircle className="text-white" size={40} />
              </div>
              <h2 className="text-white text-2xl font-bold mb-1">Bill Sent to Group!</h2>
              <p className="text-green-300 text-sm">QR codes and payment details have been delivered</p>
            </div>

            <div className="bg-white/5 rounded-xl p-5 mb-6 border border-white/10">
              <div className="flex items-center gap-2 mb-3">
                <Bell className="text-orange-400" size={20} />
                <h3 className="text-white font-semibold">Set a payment reminder?</h3>
              </div>

              {!showRemindPicker ? (
                <div className="flex gap-3">
                  <button
                    onClick={() => setShowRemindPicker(true)}
                    className="flex-1 bg-orange-500/20 hover:bg-orange-500/30 border border-orange-400/40 text-orange-300 px-4 py-3 rounded-xl text-sm font-semibold transition-all"
                  >
                    Yes, remind me
                  </button>
                  <button
                    onClick={() => setStep('overview')}
                    className="flex-1 bg-white/5 hover:bg-white/10 border border-white/10 text-white/50 px-4 py-3 rounded-xl text-sm font-semibold transition-all"
                  >
                    No thanks
                  </button>
                </div>
              ) : (
                <>
                  <p className="text-blue-200 text-sm mb-3">Nudge those who haven't paid after:</p>
                  <div className="grid grid-cols-3 gap-2 mb-4">
                    {[
                      { label: '1 min', hours: 1/60 },
                      { label: '5 min', hours: 5/60 },
                      { label: '6 hours', hours: 6 },
                      { label: '12 hours', hours: 12 },
                      { label: '1 day', hours: 24 },
                      { label: '3 days', hours: 72 },
                      { label: '5 days', hours: 120 },
                      { label: '7 days', hours: 168 },
                    ].map(opt => (
                      <button
                        key={opt.hours}
                        onClick={() => setAutoRemindHours(autoRemindHours === opt.hours ? null : opt.hours)}
                        className={`px-3 py-2.5 rounded-xl text-sm font-semibold transition-all border ${
                          autoRemindHours === opt.hours
                            ? 'bg-orange-500/20 border-orange-400/60 text-orange-300'
                            : 'bg-white/5 border-white/10 text-white/50 hover:border-white/30'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                  {autoRemindHours && (
                    <div className="bg-orange-500/10 rounded-lg p-3 mb-4 border border-orange-400/20 text-orange-200 text-xs">
                      Those who haven't paid will be nudged in {autoRemindHours >= 24 ? `${autoRemindHours / 24} day${autoRemindHours > 24 ? 's' : ''}` : autoRemindHours >= 1 ? `${autoRemindHours} hour${autoRemindHours > 1 ? 's' : ''}` : `${Math.round(autoRemindHours * 60)} min`}.
                    </div>
                  )}
                  <button
                    onClick={async () => {
                      if (autoRemindHours && sessionId) {
                        try { await setAutoRemind(sessionId, autoRemindHours); } catch {}
                      }
                      setStep('overview');
                    }}
                    disabled={!autoRemindHours}
                    className="w-full btn-primary text-white px-6 py-3 rounded-xl font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Set Reminder
                  </button>
                </>
              )}
            </div>

            {!showRemindPicker && (
              <button
                onClick={() => setStep('overview')}
                className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between"
              >
                <span>View Payment Status</span>
                <ArrowRight size={20} />
              </button>
            )}
          </div>
          );
        })()}

        {/* Payment Status Overview */}
        {step === 'overview' && splitConfirmed && (
           <div className="glass rounded-3xl p-8 animate-in">
            {/* Identity verification for View Split links */}
            {!myTelegramId && sessionPayeeTid && knownMembers.length > 0 && (
              <div className="bg-amber-500/10 rounded-xl p-4 mb-4 border border-amber-400/30">
                <div className="text-amber-300 text-xs font-semibold mb-2">Are you the payee?</div>
                <div className="flex flex-wrap gap-2">
                  {knownMembers.map(m => (
                    <button
                      key={m.id || m.name}
                      onClick={() => {
                        if (m.id) setMyTelegramId(m.id);
                        setViewerName(m.name);
                      }}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white/5 border border-white/15 text-white/60 hover:border-amber-400/50 hover:text-white transition-all"
                    >
                      {m.name}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <h2 className="text-white text-xl font-bold mb-1">Payment Status</h2>
            <p className="text-blue-200 text-sm mb-3">
              {paidCount === totalParticipants
                ? 'All payments received!'
                : `${totalParticipants - paidCount} ${totalParticipants - paidCount === 1 ? 'person needs' : 'people need'} to pay you back`}
            </p>

            <div className="bg-gradient-to-r from-blue-500/10 to-cyan-500/10 rounded-xl p-4 mb-4 border border-blue-400/20">
              <div className="flex justify-between items-center mb-2">
                <span className="text-white text-sm font-semibold">Settlement Progress</span>
                <span className="text-blue-300 mono text-sm font-bold">{paidCount}/{totalParticipants} paid</span>
              </div>
              <div className="w-full h-3 bg-white/10 rounded-full overflow-hidden mb-2">
                <div className="h-full rounded-full bg-gradient-to-r from-green-400 to-emerald-500 transition-all duration-500" style={{ width: `${settlementPct}%` }} />
              </div>
              <div className="flex justify-between items-center">
                <span className="text-white/50 text-xs">${paidAmount.toFixed(2)} collected</span>
                <span className={`text-xs font-semibold ${leftToPayPct === 0 ? 'text-green-400' : 'text-amber-400'}`}>
                  {leftToPayPct === 0 ? 'All settled!' : `${Math.round(leftToPayPct)}% left to settle`}
                </span>
              </div>
            </div>

            <div className="flex gap-1 bg-white/5 rounded-xl p-1 mb-4 border border-white/10">
              <button onClick={() => setOverviewTab('status')} className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all ${overviewTab === 'status' ? 'bg-blue-500 text-white shadow-lg' : 'text-white/50 hover:text-white'}`}>
                Status
              </button>
              <button onClick={() => setOverviewTab('history')} className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-all ${overviewTab === 'history' ? 'bg-blue-500 text-white shadow-lg' : 'text-white/50 hover:text-white'}`}>
                History
              </button>
            </div>

            {overviewTab === 'status' && (
              <>
                <div className="space-y-3 mb-4">
                  {otherParticipants.map((participant) => {
                    const status = paymentStatuses[participant];
                    const canManagePayments = isViewerThePayee;
                    return (
                    <div key={participant} className={`rounded-xl p-4 flex items-center justify-between border transition-all ${status === 'paid' ? 'bg-green-500/5 border-green-500/20' : status === 'self-confirmed' ? 'bg-amber-500/5 border-amber-500/20' : 'bg-white/5 border-white/10'}`}>
                      <div className="flex items-center gap-3">
                        {canManagePayments ? (
                          <button
                            onClick={async () => {
                              if (status !== 'paid') {
                                if (sessionId) {
                                  try { await updatePaymentStatus(sessionId, participant, 'paid', myTelegramId || undefined); } catch {}
                                }
                                confirmPayment(participant);
                              }
                            }}
                            className={`w-6 h-6 rounded-md border-2 flex items-center justify-center transition-all shrink-0 ${
                              status === 'paid'
                                ? 'bg-green-500 border-green-400'
                                : status === 'self-confirmed'
                                  ? 'bg-amber-500 border-amber-400 animate-pulse'
                                  : 'border-white/30 hover:border-blue-400'
                            }`}
                          >
                            {status === 'paid' && <CheckCircle className="text-white" size={14} />}
                            {status === 'self-confirmed' && <Clock className="text-white" size={14} />}
                          </button>
                        ) : (
                          <div className={`w-6 h-6 rounded-md border-2 flex items-center justify-center shrink-0 ${
                            status === 'paid' ? 'bg-green-500 border-green-400' : status === 'self-confirmed' ? 'bg-amber-500 border-amber-400' : 'border-white/20'
                          }`}>
                            {status === 'paid' && <CheckCircle className="text-white" size={14} />}
                            {status === 'self-confirmed' && <Clock className="text-white" size={14} />}
                          </div>
                        )}
                        <div>
                          <div className="text-white mono text-sm">@{participant}</div>
                          <div className={`mono text-xs font-semibold ${status === 'paid' ? 'text-green-400' : status === 'self-confirmed' ? 'text-amber-400' : 'text-white/50'}`}>
                            ${getResolvedAmount(participant)}
                            {status === 'self-confirmed' && <span className="ml-1 text-[10px]">— says paid</span>}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {status === 'paid' ? (
                          <>
                            {screenshotUrls[participant] && (
                              <button onClick={() => setViewingScreenshot(screenshotUrls[participant])} className="text-blue-400 text-xs font-semibold flex items-center gap-1 hover:text-blue-300"><Camera size={14} />Proof</button>
                            )}
                            <span className="text-green-400 text-xs font-semibold flex items-center gap-1"><CheckCircle size={14} />Paid</span>
                          </>
                        ) : status === 'self-confirmed' ? (
                          <span className="text-amber-400 text-xs font-semibold flex items-center gap-1"><Clock size={14} />Pending</span>
                        ) : (
                          <button onClick={() => generateQR(participant)} className="btn-primary text-white px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5"><QrCode size={14} />QR</button>
                        )}
                      </div>
                    </div>
                    );
                  })}
                </div>

                {!allPaid() && (
                  <div className="space-y-2 mb-4">
                    {isViewerThePayee && totalParticipants - paidCount > 1 && (
                      <button onClick={markAllAsPaid} className="w-full bg-green-500/10 hover:bg-green-500/20 border border-green-400/30 text-green-300 px-4 py-3 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-all">
                        <CheckCircle size={16} />
                        Mark All as Paid
                      </button>
                    )}
                    <button onClick={() => setStep('reminder')} className="w-full btn-secondary text-white px-4 py-3 rounded-xl font-semibold flex items-center justify-center gap-2">
                      <Bell size={16} />Send Reminders ({totalParticipants - paidCount} unpaid)
                    </button>
                    {autoRemindHours ? (
                      <div className="bg-orange-500/10 rounded-xl p-3 border border-orange-400/20 flex items-center justify-between">
                        <span className="text-orange-200 text-xs">Nudging unpaid in {autoRemindHours >= 24 ? `${autoRemindHours / 24} day${autoRemindHours > 24 ? 's' : ''}` : autoRemindHours >= 1 ? `${autoRemindHours} hour${autoRemindHours > 1 ? 's' : ''}` : `${Math.round(autoRemindHours * 60)} min`}</span>
                        <button onClick={async () => { setAutoRemindHours(null); if (sessionId) try { await setAutoRemind(sessionId, null); } catch {} }} className="text-orange-400 text-xs font-semibold hover:text-orange-300">Cancel</button>
                      </div>
                    ) : (
                      <button onClick={() => setStep('auto-remind-setup')} className="w-full bg-orange-500/10 hover:bg-orange-500/15 border border-orange-400/20 text-orange-300 px-4 py-3 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-all">
                        <Clock size={16} />Schedule Reminder
                      </button>
                    )}
                  </div>
                )}
              </>
            )}

            {overviewTab === 'history' && (
              <div className="mb-4">
                {paymentHistory.length === 0 ? (
                  <div className="bg-white/5 rounded-xl p-6 text-center">
                    <Clock className="text-white/20 mx-auto mb-2" size={32} />
                    <p className="text-white/40 text-sm">No payments recorded yet</p>
                    <p className="text-white/20 text-xs mt-1">Payment confirmations will appear here</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {paymentHistory.map((entry, i) => (
                      <div key={i} className="bg-green-500/5 rounded-xl p-3 border border-green-500/20 flex items-center gap-3" style={{ animation: `slideIn 0.3s ease-out ${i * 0.1}s both` }}>
                        <div className="w-8 h-8 rounded-lg bg-green-500/20 flex items-center justify-center shrink-0">
                          <CheckCircle className="text-green-400" size={16} />
                        </div>
                        <div className="flex-1">
                          <div className="text-white mono text-sm">@{entry.name}</div>
                          <div className="text-white/40 text-[10px]">Paid at {entry.time}</div>
                        </div>
                        <span className="text-green-400 mono text-sm font-semibold">${entry.amount}</span>
                      </div>
                    ))}
                    <div className="bg-white/5 rounded-lg p-3 mt-3">
                      <div className="flex justify-between text-xs">
                        <span className="text-white/50">Total Collected</span>
                        <span className="text-green-400 mono font-bold">${paymentHistory.reduce((s, e) => s + parseFloat(e.amount), 0).toFixed(2)}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {allPaid() && (
              <div className="bg-gradient-to-r from-green-500/20 to-emerald-500/20 rounded-2xl p-6 mb-4 border border-green-400/30 animate-in">
                <div className="text-center mb-4">
                  <div className="w-20 h-20 mx-auto rounded-full bg-gradient-to-br from-green-400 to-emerald-500 flex items-center justify-center animate-bounce">
                    <CheckCircle className="text-white" size={48} />
                  </div>
                  <h3 className="text-white text-xl font-bold mt-3 mb-1">All Settled!</h3>
                  <p className="text-green-200 text-sm">Everyone has paid their share.</p>
                </div>
                <div className="grid grid-cols-3 gap-2 mb-4">
                  <div className="bg-white/5 rounded-xl p-2 text-center"><div className="text-green-400 mono font-bold">${finalBillStr}</div><div className="text-white/40 text-[10px]">Total</div></div>
                  <div className="bg-white/5 rounded-xl p-2 text-center"><div className="text-blue-400 font-bold">{otherParticipants.length + 1}</div><div className="text-white/40 text-[10px]">People</div></div>
                  <div className="bg-white/5 rounded-xl p-2 text-center"><div className="text-purple-400 mono font-bold">${evenSplit ? calculateSplit() : '—'}</div><div className="text-white/40 text-[10px]">Per Person</div></div>
                </div>
                {/* Referral Section */}
                <div className="bg-blue-500/10 rounded-xl p-4 mb-4 border border-blue-400/20">
                  <div className="text-white text-sm font-bold mb-1">Share GroupPay</div>
                  <p className="text-blue-200 text-xs mb-3">Invite friends to try GroupPay for their next group bill</p>
                  <div className="flex gap-2">
                    <button onClick={() => {
                      const tg = window.Telegram?.WebApp;
                      if (tg) tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(window.location.origin)}&text=${encodeURIComponent('Try GroupPay for splitting bills!')}`);
                    }} className="flex-1 bg-[#0088cc]/20 hover:bg-[#0088cc]/30 text-[#6ab2f2] text-xs font-semibold py-2 rounded-lg transition flex items-center justify-center gap-1">Telegram</button>
                    <button onClick={() => { navigator.clipboard.writeText(window.location.origin); }} className="flex-1 bg-white/10 hover:bg-white/15 text-white/70 text-xs font-semibold py-2 rounded-lg transition flex items-center justify-center gap-1">Copy Link</button>
                  </div>
                </div>

                <button onClick={reset} className="w-full bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-400 hover:to-emerald-500 text-white px-6 py-3 rounded-xl font-semibold flex items-center justify-center gap-2 transition-all hover:-translate-y-0.5"><DollarSign size={18} />Split Another Bill</button>
              </div>
            )}
          </div>
        )}

        {/* QR Display — real QR from API */}
        {step === 'qr-display' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">Payment QR Code</h2>
            <p className="text-blue-200 text-sm mb-2">For @{selectedPayer} — paying to @{currentUser}</p>
            <p className="text-white/50 text-xs mb-6">Event: {eventName}</p>
            <div className="bg-white rounded-2xl p-6 mb-6">
              {sessionId ? (
                <img
                  src={qrUrl(sessionId, selectedPayer!)}
                  alt="Payment QR Code"
                  className="w-64 h-64 mx-auto"
                />
              ) : (
                <div className="w-64 h-64 mx-auto bg-gradient-to-br from-blue-100 to-blue-50 rounded-xl flex items-center justify-center">
                  <div className="text-center">
                    <QrCode className="text-blue-600 mx-auto mb-3" size={80} />
                    <div className="text-blue-900 font-bold text-lg mono">${getResolvedAmount(selectedPayer!)}</div>
                    <div className="text-blue-600 text-xs mt-1 mono">PayNow: +65 XXXX XXXX</div>
                  </div>
                </div>
              )}
              <div className="text-center mt-3">
                <div className="text-blue-900 font-bold text-lg mono">${getResolvedAmount(selectedPayer!)}</div>
              </div>
            </div>
            <div className="bg-blue-500/10 rounded-xl p-4 mb-6 border border-blue-400/30">
              <p className="text-blue-200 text-sm mb-3"><strong className="text-white">Next steps for @{selectedPayer}:</strong></p>
              <ol className="text-blue-200 text-sm space-y-2 list-decimal list-inside">
                <li>Save this QR code image</li>
                <li>Open PayNow/PayLah app</li>
                <li>Scan the QR code and pay</li>
                <li>Take a screenshot of payment confirmation</li>
                <li>Upload screenshot to verify payment</li>
              </ol>
            </div>
            <button onClick={() => fileInputRef.current?.click()} disabled={uploadingScreenshot} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-between mb-3">
              {uploadingScreenshot ? (
                <><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div><span>Uploading...</span></>
              ) : (
                <><span>Upload Payment Screenshot</span><Camera size={20} /></>
              )}
            </button>
            <button onClick={() => setStep('overview')} className="w-full text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back to Overview</button>
          </div>
        )}

        {/* Payment Verification */}
        {step === 'verify-payment' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <h2 className="text-white text-xl font-bold mb-2">Verify Payment</h2>
            <p className="text-blue-200 text-sm mb-6">Payment screenshot from @{selectedPayer}</p>
            <div className="bg-white rounded-2xl p-4 mb-6">
              <div className="bg-gradient-to-br from-green-50 to-emerald-50 rounded-xl p-6">
                <div className="text-center mb-4"><CheckCircle className="text-green-600 mx-auto mb-2" size={48} /><h3 className="text-green-900 font-bold text-lg mb-1">Screenshot Uploaded</h3><p className="text-green-700 text-xs">Ready for verification</p></div>
                <div className="bg-white/50 rounded-lg p-4 space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-gray-600">To:</span><span className="text-gray-900 font-semibold mono">@{currentUser}</span></div>
                  <div className="flex justify-between"><span className="text-gray-600">Amount:</span><span className="text-green-600 font-bold mono">${getResolvedAmount(selectedPayer!)}</span></div>
                  <div className="flex justify-between"><span className="text-gray-600">Date:</span><span className="text-gray-900 mono text-xs">{new Date().toLocaleDateString('en-SG', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}</span></div>
                </div>
              </div>
            </div>
            <div className="bg-yellow-500/10 rounded-xl p-4 mb-6 border border-yellow-400/30"><p className="text-yellow-200 text-sm"><strong className="text-yellow-100">Verifying payment details...</strong><br />Checking amount, recipient, and timestamp</p></div>
            <button onClick={handlePaymentVerification} disabled={verifyingPayment} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-center gap-2 disabled:opacity-50">
              {verifyingPayment ? (<><div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin"></div><span>Verifying...</span></>) : (<><CheckCircle size={20} /><span>Confirm Payment Verification</span></>)}
            </button>
            <button onClick={() => setStep('qr-display')} className="w-full mt-3 text-blue-200 hover:text-white text-sm py-2 flex items-center justify-center gap-2"><ArrowLeft size={16} />Back to QR Code</button>
          </div>
        )}

        {/* Payment Confirmed */}
        {step === 'payment-confirmed' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="text-center mb-6">
              <div className="w-20 h-20 mx-auto mb-4 rounded-full bg-gradient-to-br from-green-400 to-emerald-500 flex items-center justify-center"><CheckCircle className="text-white" size={48} /></div>
              <h2 className="text-white text-2xl font-bold mb-2">Payment Verified!</h2>
              <p className="text-green-200 text-sm">@{selectedPayer} has successfully paid</p>
            </div>
            <div className="bg-green-500/10 rounded-xl p-6 mb-6 border border-green-400/30">
              <div className="space-y-3">
                <div className="flex justify-between items-center"><span className="text-green-200">Payer:</span><span className="text-white mono font-semibold">@{selectedPayer}</span></div>
                <div className="flex justify-between items-center"><span className="text-green-200">Amount Paid:</span><span className="text-green-400 mono font-bold text-lg">${getResolvedAmount(selectedPayer!)}</span></div>
                <div className="flex justify-between items-center"><span className="text-green-200">Status:</span><span className="text-green-400 font-semibold">Confirmed</span></div>
              </div>
            </div>
            <div className="bg-blue-500/10 rounded-xl p-4 mb-6 border border-blue-400/30"><p className="text-blue-200 text-sm"><strong className="text-white">Payment recorded!</strong><br />The bot has updated @{selectedPayer}'s status to "Paid" and notified all participants.</p></div>
            <button onClick={() => setStep('overview')} className="w-full btn-primary text-white px-6 py-4 rounded-xl font-semibold flex items-center justify-center gap-2"><span>Back to Payment Overview</span><ArrowRight size={20} /></button>
          </div>
        )}

        {/* Reminders */}
        {step === 'reminder' && (
          <div className="glass rounded-3xl p-8 animate-in">
            <div className="flex items-center gap-4 mb-2">
              <div className="relative">
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-orange-500 to-red-500 flex items-center justify-center">
                  <Bell className="text-white animate-pulse" size={28} />
                </div>
                <span className="absolute -top-2 -right-2 bg-red-500 text-white text-xs font-bold w-6 h-6 rounded-full flex items-center justify-center">
                  {participants.filter(p => p.trim() && paymentStatuses[p] !== 'paid').length}
                </span>
              </div>
              <div>
                <h2 className="text-white text-xl font-bold">Send Payment Reminders</h2>
                <p className="text-orange-200 text-sm">Nudge unpaid participants via Telegram</p>
              </div>
            </div>
            <div className="bg-gradient-to-r from-orange-500/10 to-red-500/10 rounded-xl p-4 mb-6 border border-orange-400/20">
              <div className="text-white/60 text-xs font-semibold mb-2">MESSAGE PREVIEW</div>
              <p className="text-white text-sm italic">
                "Hey! Just a friendly reminder — you owe <span className="text-orange-300 mono font-semibold">${evenSplit ? calculateSplit() : '...'}</span> for the group bill of <span className="text-orange-300 mono font-semibold">${finalBillStr}</span>. Pay via the QR code link!"
              </p>
            </div>
            <div className="space-y-3 mb-6">
              {participants.filter(p => p.trim() && paymentStatuses[p] !== 'paid').map((participant, i) => {
                const isSent = !!remindersSent[participant];
                const isSending = sendingReminder === participant;
                return (
                  <div key={participant} className={`rounded-xl p-4 border transition-all duration-500 ${isSent ? 'bg-green-500/10 border-green-400/30' : 'bg-orange-500/10 border-orange-400/30 hover:bg-orange-500/15'}`} style={{ animation: `slideIn 0.4s ease-out ${i * 0.1}s both` }}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-lg flex items-center justify-center transition-colors duration-500 ${isSent ? 'bg-green-500/20' : 'bg-orange-500/20'}`}>
                          {isSent ? <CheckCircle className="text-green-400" size={20} /> : <Clock className={`text-orange-400 ${!isSending ? 'animate-pulse' : ''}`} size={20} />}
                        </div>
                        <div>
                          <div className="text-white mono text-sm">@{participant}</div>
                          <div className={`text-xs font-semibold ${isSent ? 'text-green-300' : 'text-orange-300'}`}>
                            {isSent ? `Sent at ${remindersSent[participant]}` : 'Waiting for payment'}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-orange-400 mono text-sm font-bold">${getResolvedAmount(participant)}</span>
                        <button onClick={() => handleSendReminder(participant)} disabled={isSent || isSending} className={`px-4 py-2 rounded-lg text-sm font-semibold flex items-center gap-2 transition-all ${isSent ? 'bg-green-500/20 text-green-400 cursor-default' : isSending ? 'btn-primary text-white opacity-70' : 'btn-primary text-white hover:-translate-y-0.5'}`}>
                          {isSending ? (<><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>Sending</>) : isSent ? (<><CheckCircle size={14} />Sent</>) : (<><Bell size={14} />Remind</>)}
                        </button>
                      </div>
                    </div>
                    {isSent && (
                      <div className="mt-3 pt-3 border-t border-green-400/20 animate-in">
                        <div className="flex items-center gap-2 text-green-300 text-xs"><div className="w-1.5 h-1.5 rounded-full bg-green-400"></div>Notification delivered via Telegram</div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="flex gap-3">
              <button onClick={() => setStep('overview')} className="flex-1 btn-secondary text-white px-6 py-3 rounded-xl font-semibold flex items-center justify-center gap-2"><ArrowLeft size={16} />Back</button>
              {participants.filter(p => p.trim() && paymentStatuses[p] !== 'paid' && !remindersSent[p]).length > 0 && (
                <button onClick={handleSendAllReminders} className="flex-1 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-400 hover:to-red-400 text-white px-6 py-3 rounded-xl font-semibold flex items-center justify-center gap-2 transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-orange-500/30"><Bell size={16} />Send All</button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Step Indicator */}
      <div className="max-w-md mx-auto mt-6 text-center">
        <div className="text-blue-300/50 text-xs mono">
          {step === 'start' && 'START'}
          {step === 'ocr-choice' && 'CHOOSE INPUT METHOD'}
          {step === 'ocr-scan' && 'SCANNING RECEIPT'}
          {step === 'ocr-result' && 'RECEIPT PROCESSED'}
          {step === 'manual-bill' && 'ENTER BILL'}
          {step === 'who-paid' && 'WHO PAID?'}
          {step === 'participants' && 'ADD PARTICIPANTS'}
          {step === 'item-assign' && 'ASSIGN ITEMS'}
          {step === 'split-type' && 'CHOOSE SPLIT TYPE'}
          {step === 'custom-split' && 'CUSTOM SPLIT'}
          {step === 'review-split' && 'REVIEW SPLIT'}
          {step === 'auto-remind-setup' && 'AUTO-REMIND'}
          {step === 'overview' && 'PAYMENT STATUS'}
          {step === 'qr-display' && 'QR CODE'}
          {step === 'verify-payment' && 'VERIFYING PAYMENT'}
          {step === 'payment-confirmed' && 'PAYMENT CONFIRMED'}
          {step === 'reminder' && 'REMINDERS'}
        </div>
      </div>

      {/* Screenshot viewer modal */}
      {viewingScreenshot && (
        <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4" onClick={() => setViewingScreenshot(null)}>
          <div className="relative max-w-lg w-full" onClick={e => e.stopPropagation()}>
            <button onClick={() => setViewingScreenshot(null)} className="absolute -top-10 right-0 text-white/60 hover:text-white text-sm font-semibold">Close</button>
            <img src={viewingScreenshot} alt="Payment screenshot" className="w-full rounded-xl border border-white/20" />
          </div>
        </div>
      )}
    </div>
  );
}
