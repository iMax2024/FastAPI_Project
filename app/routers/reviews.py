from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.products import Product as ProductModel
from app.models.reviews import Review as ReviewModel
from app.schemas import ReviewSchema, ReviewCreate
from app.db_depends import get_async_db
from app.auth import get_current_buyer
from app.models.users import User as UserModel
from app.auth import get_current_user
from app.services import update_product_rating

router = APIRouter(prefix="/reviews", tags=["reviews"])

@router.get("/", response_model=list[ReviewSchema])
async def get_all_reviews(db: AsyncSession = Depends(get_async_db)):
    """
    Возвращает список всех активных отзывов.
    """
    result = await db.scalars(select(ReviewModel).where(ReviewModel.is_active == True))
    return result.all()


@router.post("/", response_model=ReviewSchema, status_code=status.HTTP_201_CREATED)
async def create_review(
    review: ReviewCreate,
    current_user: UserModel = Depends(get_current_buyer),
    db: AsyncSession = Depends(get_async_db)):
    """
    Создаёт новый отзыв о товаре.
    """
    # Проверяем, существует ли товар
    product = await db.scalar(
        select(ProductModel).where(ProductModel.id == review.product_id, ProductModel.is_active == True)
    )
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Product does not exist or inactive")

    # 2. Проверяем, не оставлял ли пользователь отзыв ранее (чтобы избежать дублей)
    existing_review = await db.scalar(
        select(ReviewModel).where(
            ReviewModel.product_id == review.product_id,
            ReviewModel.user_id == current_user.id,
            ReviewModel.is_active == True
        )
    )
    if existing_review:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have already reviewed this product")

    # Создаём товар
    db_review = ReviewModel(**review.model_dump(), user_id=current_user.id)
    db.add(db_review)
    await db.commit()
    await update_product_rating(db, product.id)
    await db.refresh(db_review)
    return db_review

@router.delete("/{review_id}")
async def delete_review(review_id: int, current_user: UserModel = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    # Проверяем, существует ли отзыв
    review = await db.scalar(
        select(ReviewModel).where(ReviewModel.id == review_id, ReviewModel.is_active == True)
    )
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Review does not exist or inactive")
    if current_user.id != review.user_id and current_user.role != 'admin':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only admin or byuer can delete this review")

    review.is_active = False
    await db.commit()
    await update_product_rating(db, review.product_id)
    return {"message": "Review deleted"}