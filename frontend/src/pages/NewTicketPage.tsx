import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Category, Priority, TicketCreateInput } from "../types/ticket";
import { PRIORITY_OPTIONS } from "../types/ticket";

const emptyForm: TicketCreateInput = {
  caller_name: "",
  phone_number: "",
  email: "",
  category_id: "",
  priority: undefined,
  description: "",
};

export function NewTicketPage() {
  const navigate = useNavigate();
  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState<TicketCreateInput>(emptyForm);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .listCategories()
      .then((cats) => {
        setCategories(cats);
        if (cats.length > 0) {
          setForm((f) => ({ ...f, category_id: cats[0].id }));
        }
      })
      .catch(() => setSubmitError("Failed to load categories"));
  }, []);

  function validate(): boolean {
    const errors: Record<string, string> = {};
    if (!form.caller_name.trim()) errors.caller_name = "Caller name is required";
    if (!form.phone_number.trim()) errors.phone_number = "Phone number is required";
    if (!form.category_id) errors.category_id = "Category is required";
    if (!form.description.trim()) errors.description = "Description is required";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!validate()) return;

    setSubmitting(true);
    try {
      const payload: TicketCreateInput = {
        ...form,
        email: form.email?.trim() ? form.email.trim() : undefined,
      };
      const ticket = await api.createTicket(payload);
      navigate(`/tickets/${ticket.id}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Failed to create ticket");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <h1>New Ticket</h1>
      <form onSubmit={handleSubmit}>
        <Field label="Caller Name" error={fieldErrors.caller_name}>
          <input
            value={form.caller_name}
            onChange={(e) => setForm({ ...form, caller_name: e.target.value })}
          />
        </Field>

        <Field label="Phone Number" error={fieldErrors.phone_number}>
          <input
            value={form.phone_number}
            onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
            placeholder="+15551234567"
          />
        </Field>

        <Field label="Email (optional)">
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </Field>

        <Field label="Category" error={fieldErrors.category_id}>
          <select
            value={form.category_id}
            onChange={(e) => setForm({ ...form, category_id: e.target.value })}
          >
            {categories.map((cat) => (
              <option key={cat.id} value={cat.id}>
                {cat.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Priority (optional — defaults from category)">
          <select
            value={form.priority ?? ""}
            onChange={(e) =>
              setForm({ ...form, priority: (e.target.value || undefined) as Priority | undefined })
            }
          >
            <option value="">Default</option>
            {PRIORITY_OPTIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Description" error={fieldErrors.description}>
          <textarea
            rows={6}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </Field>

        {submitError && <p style={{ color: "#dc2626" }}>{submitError}</p>}

        <button type="submit" disabled={submitting}>
          {submitting ? "Submitting…" : "Submit Ticket"}
        </button>
      </form>
    </div>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label style={{ display: "block", marginBottom: 4, fontWeight: 600 }}>{label}</label>
      {children}
      {error && <div style={{ color: "#dc2626", fontSize: 13, marginTop: 4 }}>{error}</div>}
    </div>
  );
}
