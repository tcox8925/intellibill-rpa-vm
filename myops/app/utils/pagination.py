from sqlalchemy import func, asc, desc, select


def paginate(
    query,
    db,
    model,
    page: int = 1,
    page_size: int = 10,
    get_total_count: bool = False, # Enable to get total count of table
    sort_column: str = None,
    sort_order: str = "asc"
):
    """
    Paginate a SQLAlchemy query with sorting support.
    
    Args:
        query: SQLAlchemy query
        db: database session
        model: SQLAlchemy model class (needed for getattr)
        page: current page
        page_size: number of records per page
        sort_column: column name to sort by
        sort_order: 'asc' or 'desc'
    """
    try:
        # Apply sorting if valid
        if sort_column and hasattr(model, sort_column):
            column_attr = getattr(model, sort_column)
            if sort_order.lower() == "desc":
                query = query.order_by(desc(column_attr))
            else:
                query = query.order_by(asc(column_attr))

        # Total count (count rows in the filtered query)
        total_count = query.count()

        # Apply pagination
        results = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "items": results,
        }
    except Exception as e:
        raise
