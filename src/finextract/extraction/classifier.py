from __future__ import annotations

import re

from finextract.domain import DocumentType

_CREDIT_NOTE_KEYWORDS = [
    "CREDIT NOTE",
    "CREDIT MEMO",
    "GUTSCHRIFT",
    "AVERE",
    "NOTA DE CRÉDITO",
    "NOTA DE CREDITO",
]

_INVOICE_KEYWORDS = [
    "TAX INVOICE",
    "INVOICE #",
    "INVOICE NO",
    "INVOICE NUMBER",
    "FACTURE",
    "RECHNUNG",
    "FATTURA",
    "FACTURA",
    "INVOICE",
]

# Strong invoice signals: the document is definitely transactional with a reference
_INVOICE_STRONG_KEYWORDS = [
    "INVOICE NO",
    "INVOICE NUMBER",
    "INVOICE #",
    "INV NO",
    "INV NUMBER",
]

_RECEIPT_KEYWORDS = [
    "RECEIPT",
    "REÇU",
    "RECU",
    "QUITTUNG",
]

# POS / informal receipt signals — common on SA till slips and email receipts.
# "SUBTOTAL" alone is too common on invoices and excluded.
_RECEIPT_POS_KEYWORDS = [
    "AMOUNT DUE",
    "AMOUNT CHARGED",
    "PAYMENT RECEIVED",
    "THANKS FOR RIDING",
    "THANK YOU FOR",
    "AMOUNT PAID",
    "PAID IN CASH",
    "PAID BY CARD",
    "TABLE NO",
    "TABLE NUMBER",
    "SALE #",
    "SALE NO",
    "RECEIPT NO",
    "BOOKING CONFIRMED",
    "E-TICKET",
    "TRIP FARE",
    "YOCO RECEIPTS",  # Yoco POS platform used in SA
]

_STATEMENT_KEYWORDS = [
    "TRANSACTION STATEMENT",
    "STATEMENT OF ACCOUNT",
    "ACCOUNT STATEMENT",
    "BALANCE STATEMENT",
    "CLOSING BALANCE",
    "OPENING BALANCE",
    "BALANCE B/F",
    "BALANCE BF",
    "START DATE",
    "END DATE",
]

_PROOF_OF_PAYMENT_KEYWORDS = [
    "PROOF OF PAYMENT",
    "EFT CONFIRMATION",
    "PAYMENT CONFIRMATION",
    "PROOF OF TRANSFER",
    "ELECTRONIC FUNDS TRANSFER",
    "AUTHORISED",  # bank EFT slips in SA use this spelling
    "TRACE NO",
    "AUTHORISATION CODE",
    "AUTHORISATION NO",
]

_PURCHASE_ORDER_KEYWORDS = [
    "PURCHASE ORDER",
    "ORDER CONFIRMATION",
    "P.O. NUMBER",
    "PO NUMBER",
]

# Secondary signal: invoice number patterns boost invoice confidence
_INVOICE_NUMBER_RE = re.compile(
    r"(?:INV[-\s]|INVOICE\s*(?:#|NO\.?|NUMBER)\s*[:\-]?\s*\S)",
    re.IGNORECASE,
)


def _count_hits(text_upper: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text_upper)


class KeywordClassifier:
    """Keyword-based document type classifier."""

    def classify(self, text: str) -> tuple[DocumentType, float]:
        """Return (DocumentType, confidence) for the given document text."""
        text_upper = text.upper()

        credit_hits = _count_hits(text_upper, _CREDIT_NOTE_KEYWORDS)
        invoice_hits = _count_hits(text_upper, _INVOICE_KEYWORDS)
        invoice_strong_hits = _count_hits(text_upper, _INVOICE_STRONG_KEYWORDS)
        receipt_hits = _count_hits(text_upper, _RECEIPT_KEYWORDS)
        receipt_pos_hits = _count_hits(text_upper, _RECEIPT_POS_KEYWORDS)
        po_hits = _count_hits(text_upper, _PURCHASE_ORDER_KEYWORDS)
        statement_hits = _count_hits(text_upper, _STATEMENT_KEYWORDS)
        pop_hits = _count_hits(text_upper, _PROOF_OF_PAYMENT_KEYWORDS)

        # Combine receipt signals: formal "RECEIPT" word or POS signals
        total_receipt_hits = receipt_hits + receipt_pos_hits

        # Secondary invoice signal
        inv_num_bonus = 0.03 if _INVOICE_NUMBER_RE.search(text) else 0.0

        # If a document has "TAX INVOICE" but also strong POS receipt signals and
        # no strong invoice-number evidence, treat it as a receipt (SA till slips).
        invoice_only_tax_inv = invoice_hits >= 1 and invoice_strong_hits == 0
        has_strong_pos = receipt_pos_hits >= 2

        candidates: list[tuple[DocumentType, float, int]] = []

        if credit_hits:
            candidates.append((DocumentType.CREDIT_NOTE, 0.95, credit_hits))

        if invoice_hits and not (invoice_only_tax_inv and has_strong_pos):
            candidates.append((DocumentType.INVOICE, 0.90 + inv_num_bonus, invoice_hits))

        if total_receipt_hits or (invoice_only_tax_inv and has_strong_pos):
            # Boost confidence when we have formal "RECEIPT" keyword
            base = 0.88 if receipt_hits else 0.80
            candidates.append((DocumentType.RECEIPT, base, total_receipt_hits or 1))

        if po_hits:
            candidates.append((DocumentType.PURCHASE_ORDER, 0.85, po_hits))

        if statement_hits:
            candidates.append((DocumentType.STATEMENT, 0.88, statement_hits))

        if pop_hits:
            candidates.append((DocumentType.PROOF_OF_PAYMENT, 0.87, pop_hits))

        if not candidates:
            return DocumentType.UNKNOWN, 0.0

        # Priority: credit_note > invoice > receipt/pop > statement > purchase_order
        _priority = {
            DocumentType.CREDIT_NOTE: 6,
            DocumentType.INVOICE: 5,
            DocumentType.RECEIPT: 4,
            DocumentType.PROOF_OF_PAYMENT: 4,
            DocumentType.STATEMENT: 3,
            DocumentType.PURCHASE_ORDER: 2,
        }

        best = max(
            candidates,
            key=lambda c: (c[2] * _priority.get(c[0], 0), c[1]),
        )
        return best[0], min(best[1], 1.0)
