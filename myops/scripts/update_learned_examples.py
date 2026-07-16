"""
Update JAI learned examples from successful conversations.

Run from the backend root:
    python -m scripts.update_learned_examples

This script:
1. Mines successful (query, SQL) pairs from jay_messages (thumbs-up or implicit acceptance)
2. Mines correction pairs (thumbs-down followed by thumbs-up in same conversation)
3. Finds failed pipeline runs and stores them as negative examples
4. Generates embeddings and upserts them into jay_knowledge_embeddings
5. Reports summary statistics
"""

import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    print("=" * 60)
    print("JAI Learned Examples Update")
    print("=" * 60)

    # ---------------------------------------------------------
    # Step 1: Import self_learning module
    # ---------------------------------------------------------
    print("\n[1/5] Importing self-learning module...")

    try:
        from app.utils.jay.self_learning import (
            mine_successful_pairs,
            mine_correction_pairs,
            store_negative_example,
            update_learned_examples,
        )
    except ImportError as e:
        print(f"  ERROR: Could not import self_learning: {e}")
        print("  Please ensure app/utils/jay/self_learning.py exists")
        print("  (Task 6.3 of the JAI Intelligence Upgrade must be completed first).")
        sys.exit(1)

    print("  Module imported successfully.")

    # ---------------------------------------------------------
    # Step 2: Mine and embed successful pairs
    # ---------------------------------------------------------
    print("\n[2/5] Mining successful pairs and generating embeddings...")

    session = SessionLocal()
    try:
        # First, show how many pairs are available
        pairs = mine_successful_pairs(session)
        print(f"  Found {len(pairs)} unique successful query/SQL pairs.")

        if pairs:
            # Show a few examples
            print(f"\n  Sample successful pairs (first 3):")
            for p in pairs[:3]:
                query_preview = p["query"][:80] + "..." if len(p["query"]) > 80 else p["query"]
                print(f"    - [{p['module'] or 'unknown'}] {query_preview}")
                print(f"      Rating: {p['rating'] if p['rating'] is not None else 'unrated (implicit acceptance)'}")

        # Now embed all learned examples (this also processes correction pairs)
        print(f"\n  Embedding {len(pairs)} pairs...")
        stats = update_learned_examples(session)
        print(f"  Mined:       {stats['mined']}")
        print(f"  Embedded:    {stats['embedded']}")
        print(f"  Corrections: {stats['corrections']}")

    except Exception as e:
        print(f"  ERROR: {e}")
        raise
    finally:
        session.close()

    # ---------------------------------------------------------
    # Step 3: Mine correction pairs (show details)
    # ---------------------------------------------------------
    print("\n[3/5] Mining correction pairs (thumbs-down -> thumbs-up)...")

    session = SessionLocal()
    try:
        corrections = mine_correction_pairs(session)
        print(f"  Found {len(corrections)} correction pairs.")

        if corrections:
            print(f"\n  Sample correction pairs (first 3):")
            for cp in corrections[:3]:
                neg_query = cp["negative"]["query"][:60] + "..." if len(cp["negative"]["query"]) > 60 else cp["negative"]["query"]
                pos_query = cp["positive"]["query"][:60] + "..." if len(cp["positive"]["query"]) > 60 else cp["positive"]["query"]
                print(f"    Conversation: {cp['conversation_id'][:8]}...")
                print(f"      BAD:  {neg_query}")
                print(f"      GOOD: {pos_query}")
    except Exception as e:
        print(f"  ERROR mining corrections: {e}")
    finally:
        session.close()

    # ---------------------------------------------------------
    # Step 4: Store negative examples from failed pipeline runs
    # ---------------------------------------------------------
    print("\n[4/5] Storing negative examples from failed pipeline runs...")

    session = SessionLocal()
    try:
        # Find recent assistant messages with format='error' that had SQL generated
        # (i.e., the pipeline generated SQL but it failed at execution)
        failed_sql = text("""
            SELECT
                m_user.content AS query,
                m_asst.data->>'_sql' AS sql,
                m_asst.content AS error_message,
                m_asst.conversation_id
            FROM wpo.jay_messages m_asst
            JOIN wpo.jay_messages m_user
                ON m_user.conversation_id = m_asst.conversation_id
                AND m_user.role = 'user'
                AND m_user.created_at < m_asst.created_at
                AND NOT EXISTS (
                    SELECT 1 FROM wpo.jay_messages mx
                    WHERE mx.conversation_id = m_asst.conversation_id
                        AND mx.role = 'user'
                        AND mx.created_at > m_user.created_at
                        AND mx.created_at < m_asst.created_at
                )
            WHERE m_asst.role = 'assistant'
                AND m_asst.format = 'error'
                AND m_asst.data->>'_sql' IS NOT NULL
                AND m_asst.data->>'_sql' != ''
                AND m_asst.created_at >= NOW() - INTERVAL '30 days'
            ORDER BY m_asst.created_at DESC
            LIMIT 100
        """)

        result = session.execute(failed_sql)
        rows = result.fetchall()
        print(f"  Found {len(rows)} failed pipeline runs with SQL in last 30 days.")

        stored = 0
        for row in rows:
            success = store_negative_example(
                query=row.query,
                failed_sql=row.sql,
                error_message=row.error_message or "Query failed",
                db_session=session,
            )
            if success:
                stored += 1

        print(f"  Stored {stored} negative examples.")

        # Also store thumbs-downed messages as negative examples
        thumbs_down_sql = text("""
            SELECT
                m_user.content AS query,
                m_asst.data->>'_sql' AS sql,
                m_asst.content AS response_message,
                m_asst.conversation_id
            FROM wpo.jay_messages m_asst
            JOIN wpo.jay_messages m_user
                ON m_user.conversation_id = m_asst.conversation_id
                AND m_user.role = 'user'
                AND m_user.created_at < m_asst.created_at
                AND NOT EXISTS (
                    SELECT 1 FROM wpo.jay_messages mx
                    WHERE mx.conversation_id = m_asst.conversation_id
                        AND mx.role = 'user'
                        AND mx.created_at > m_user.created_at
                        AND mx.created_at < m_asst.created_at
                )
            WHERE m_asst.role = 'assistant'
                AND m_asst.rating = -1
                AND m_asst.data->>'_sql' IS NOT NULL
                AND m_asst.data->>'_sql' != ''
                AND m_asst.created_at >= NOW() - INTERVAL '30 days'
            ORDER BY m_asst.created_at DESC
            LIMIT 100
        """)

        result = session.execute(thumbs_down_sql)
        thumbs_down_rows = result.fetchall()
        print(f"  Found {len(thumbs_down_rows)} thumbs-downed messages with SQL in last 30 days.")

        td_stored = 0
        for row in thumbs_down_rows:
            success = store_negative_example(
                query=row.query,
                failed_sql=row.sql,
                error_message=f"User gave thumbs-down. Response was: {(row.response_message or '')[:200]}",
                db_session=session,
            )
            if success:
                td_stored += 1

        print(f"  Stored {td_stored} thumbs-down negative examples.")

    except Exception as e:
        print(f"  ERROR: {e}")
        raise
    finally:
        session.close()

    # ---------------------------------------------------------
    # Step 5: Verify
    # ---------------------------------------------------------
    _verify()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)


def _verify():
    """Query jay_knowledge_embeddings for learned entries and print stats."""
    print("\n[5/5] Verifying learned examples...")
    session = SessionLocal()
    try:
        result = session.execute(text(
            "SELECT COUNT(*), "
            "COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END), "
            "AVG(score_weight) "
            "FROM wpo.jay_knowledge_embeddings "
            "WHERE category = 'learned'"
        ))
        row = result.fetchone()
        print(f"  Total learned entries:   {row[0]}")
        print(f"  With embeddings:         {row[1]}")
        print(f"  Avg score_weight:        {row[2]:.3f}" if row[2] else "  Avg score_weight:        N/A")

        # Check negative examples too
        result = session.execute(text(
            "SELECT COUNT(*), AVG(score_weight) "
            "FROM wpo.jay_knowledge_embeddings "
            "WHERE category = 'negative_example'"
        ))
        neg_row = result.fetchone()
        print(f"\n  Total negative examples: {neg_row[0]}")
        print(f"  Avg neg score_weight:    {neg_row[1]:.3f}" if neg_row[1] else "  Avg neg score_weight:    N/A")

        # Show category distribution
        result = session.execute(text(
            "SELECT category, COUNT(*) as cnt "
            "FROM wpo.jay_knowledge_embeddings "
            "GROUP BY category "
            "ORDER BY cnt DESC"
        ))
        rows = result.fetchall()
        if rows:
            print(f"\n  Knowledge embedding categories:")
            for r in rows:
                print(f"    {r[0]}: {r[1]} entries")
    except Exception as e:
        print(f"  Verification query failed: {e}")
        print("  The jay_knowledge_embeddings table may not exist yet.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
