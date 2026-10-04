import datetime as dt

from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


class Godown(Base):
    """A physical storage location (e.g. Manesar, Daultabad). Stock, GRN,
    Dispatch, Billing, and freight rates are all scoped to a Godown — a
    dealer or product is shared across godowns, but the transactions that
    move material are not."""
    __tablename__ = "godowns"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="staff")  # "admin" or "staff"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Product(Base):
    """A product is company-wide (shared across godowns).

    `bag_weight_mt` is the weight of ONE bag of this product in metric tonnes.
    Most cement here is a 50 kg bag (0.05 MT), which is what the global
    `bag_weight_mt` setting holds — but not everything is: JSW Microfine is a
    20 kg bag (0.02 MT), so 1 MT is 50 bags of it, not 20. Left NULL, the
    product falls back to the global setting, so adding this column changed
    nothing for the products that were already correct."""
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    bag_weight_mt = Column(Float, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    @property
    def bag_weight_kg(self):
        """The same thing in the unit everyone actually says out loud."""
        return round(self.bag_weight_mt * 1000) if self.bag_weight_mt else None


class Dealer(Base):
    __tablename__ = "dealers"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    sap_code = Column(String(40), unique=True, nullable=True, index=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class Transporter(Base):
    __tablename__ = "transporters"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    sap_code = Column(String(40), unique=True, nullable=True, index=True)
    contact = Column(String(100), nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class OpeningStock(Base):
    __tablename__ = "opening_stock"
    id = Column(Integer, primary_key=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    bags = Column(Float, nullable=False, default=0)
    as_of_date = Column(Date, nullable=False)

    godown = relationship("Godown")
    product = relationship("Product")

    __table_args__ = (UniqueConstraint("godown_id", "product_id", name="uq_godown_product_opening"),)


class GRN(Base):
    """Goods Receipt Note — cement received from JSW plant, after SAP GRN is done."""
    __tablename__ = "grn"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    sap_grn_no = Column(String(60), unique=True, nullable=True)  # blank allowed, but no duplicates
    invoice_no = Column(String(60), nullable=True)
    vehicle_no = Column(String(30), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    bags_invoice = Column(Float, nullable=False, default=0)
    bags_received = Column(Float, nullable=False, default=0)
    source_plant = Column(String(120), nullable=True)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Which SAP import produced this row. NULL for anything entered by hand,
    # and for rows that predate the column (those are backfilled on upgrade).
    import_log_id = Column(Integer, ForeignKey("import_log.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    product = relationship("Product")
    godown = relationship("Godown")

    @property
    def shortage_excess(self):
        return (self.bags_received or 0) - (self.bags_invoice or 0)


class Dispatch(Base):
    """Material dispatched out of the godown (secondary transportation)."""
    __tablename__ = "dispatch"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    dc_no = Column(String(60), nullable=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    destination = Column(String(150), nullable=True)
    district = Column(String(80), nullable=True, index=True)
    pincode = Column(String(10), nullable=True, index=True)
    vehicle_no = Column(String(30), nullable=True, index=True)
    transporter_id = Column(Integer, ForeignKey("transporters.id"), nullable=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    bags = Column(Float, nullable=False, default=0)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    dealer = relationship("Dealer")
    transporter = relationship("Transporter")
    product = relationship("Product")
    godown = relationship("Godown")


class Billing(Base):
    """Invoice raised on the dealer for material billed from the godown."""
    __tablename__ = "billing"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    invoice_no = Column(String(60), nullable=True)
    dealer_id = Column(Integer, ForeignKey("dealers.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    bags = Column(Float, nullable=False, default=0)
    amount = Column(Float, nullable=False, default=0)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Which SAP import produced this row. NULL for anything entered by hand,
    # and for rows that predate the column (those are backfilled on upgrade).
    import_log_id = Column(Integer, ForeignKey("import_log.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    dealer = relationship("Dealer")
    product = relationship("Product")
    godown = relationship("Godown")


class FreightRateCard(Base):
    """Freight rate card by district/pincode (Gurugram area). Two rates are kept
    side by side deliberately: what you pay the transporter is not the same
    number as what you claim back from JSW."""
    __tablename__ = "freight_rate_card"
    id = Column(Integer, primary_key=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    district = Column(String(80), nullable=False, index=True)
    pincode = Column(String(10), nullable=True, index=True)
    transporter_rate_per_mt = Column(Float, nullable=False, default=0)
    company_claim_rate_per_mt = Column(Float, nullable=False, default=0)
    remarks = Column(Text, nullable=True)

    godown = relationship("Godown")

    __table_args__ = (UniqueConstraint("godown_id", "district", "pincode", name="uq_godown_district_pincode"),)


class SapProductMap(Base):
    """Maps a SAP material/brand code (as it appears in the Sale / Material In
    exports) to one of our Products. New codes seen during import that have
    no row here are flagged for an admin to map instead of being guessed at."""
    __tablename__ = "sap_product_map"
    id = Column(Integer, primary_key=True)
    sap_code = Column(String(60), unique=True, nullable=False, index=True)
    sap_description = Column(String(200), nullable=True)  # informational, from the file
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)  # null = intentionally ignored

    product = relationship("Product")


class ImportLog(Base):
    """Audit trail of every SAP file import, so it's always clear what was
    pulled in, when, by whom, and what got skipped and why."""
    __tablename__ = "import_log"
    id = Column(Integer, primary_key=True)
    import_type = Column(String(20), nullable=False)  # "sale" or "material_in"
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=True)
    filename = Column(String(255), nullable=True)
    imported_at = Column(DateTime, default=dt.datetime.utcnow)
    imported_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    rows_total = Column(Integer, default=0)
    rows_imported = Column(Integer, default=0)
    rows_skipped_duplicate = Column(Integer, default=0)
    rows_skipped_unmapped = Column(Integer, default=0)
    rows_skipped_pending = Column(Integer, default=0)
    rows_skipped_other = Column(Integer, default=0)
    details = Column(Text, nullable=True)  # short human-readable summary, e.g. unmapped codes seen

    user = relationship("User")
    godown = relationship("Godown")


class FreightEntry(Base):
    """The freight actually agreed for one dispatch, typed in by hand.

    The rate card computes a default from ₹/MT by district, which is the
    agreed rate. This is what really happened on that truck: a negotiated
    figure, a return-load discount, or simply the amount for a district with
    no rate card entry at all. Where a row exists here it WINS over the card,
    and the trip stops being reported as unrated.

    One row per dispatch, so the figure rolls up correctly into both the
    vehicle-wise and transporter-wise views without being counted twice.

    `freight_claim` is the other half: what is claimed back from JSW for the
    same trip. Both sides are needed, because the Claims page reads the margin
    as claimable minus paid — filling in only what was paid would drive that
    margin deeply negative and wrong.
    """
    __tablename__ = "freight_entry"
    id = Column(Integer, primary_key=True)
    dispatch_id = Column(Integer, ForeignKey("dispatch.id"), nullable=False, unique=True, index=True)
    freight_paid = Column(Float, nullable=True)      # to the transporter
    freight_claim = Column(Float, nullable=True)     # from JSW
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    dispatch = relationship("Dispatch")


class SapStockSnapshot(Base):
    """What SAP says is in the godown, as of one date, for one product.

    This comes from the SAP actual-stock statement, not from our own
    transactions, which is the whole point: it is the independent figure our
    computed physical stock gets reconciled against.

    One row per (godown, product, as_of_date) — re-uploading the same
    statement date replaces the figure instead of adding a second one, the
    same idea as the Material Document / Invoice No. keys on GRN and Billing.

    Stored in BAGS, like every other quantity in this app. A statement in MT
    is converted on the way in with the product's own bag weight, so
    Microfine's 20 kg bag is honoured."""
    __tablename__ = "sap_stock_snapshot"
    id = Column(Integer, primary_key=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    bags = Column(Float, nullable=False, default=0)
    source = Column(String(20), nullable=False, default="pdf")  # "pdf" or "manual"
    filename = Column(String(255), nullable=True)
    import_log_id = Column(Integer, ForeignKey("import_log.id"), nullable=True, index=True)
    remarks = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    godown = relationship("Godown")
    product = relationship("Product")

    __table_args__ = (
        UniqueConstraint("godown_id", "product_id", "as_of_date", name="uq_sap_stock_godown_product_date"),
    )


class SapStockAdjustment(Base):
    """A known, permanent discrepancy between SAP stock and physical stock.

    The real case this exists for: material short at a godown handover. SAP
    still carries stock that physically is not there, and no amount of
    GRN/dispatch/billing entry will ever close that gap, so it has to be
    recorded once and then subtracted on every reconciliation.

    `bags` is the SHORTAGE: how many bags SAP over-states by. It is
    subtracted from the SAP side. A negative value therefore means SAP
    under-states (an excess), which reconciles the other way.

    Several rows per product are allowed and are summed — one handover, one
    row, each with its own date and reason, rather than one number that gets
    silently overwritten and loses its history."""
    __tablename__ = "sap_stock_adjustment"
    id = Column(Integer, primary_key=True)
    godown_id = Column(Integer, ForeignKey("godowns.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    bags = Column(Float, nullable=False, default=0)
    as_of_date = Column(Date, nullable=True)
    reason = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    godown = relationship("Godown")
    product = relationship("Product")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(60), primary_key=True)
    value = Column(String(255), nullable=False)
