/*
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#define USING_LOG_PREFIX SQL_ENG
#include "ob_expr_ai_embed.h"
#include "query/ai/ob_ai_endpoint_resolver.h"
#include "share/rc/ob_server_runtime.h"
#include "sql/engine/expr/ob_expr_util.h"

using namespace oceanbase::common;
using namespace oceanbase::sql;

namespace oceanbase 
{
namespace sql 
{

// The dimension of an embedding when the caller does not ask for a specific
// one.
static constexpr int64_t OB_AI_DEFAULT_EMBED_DIMENSION = 768;

ObExprAIEmbed::ObExprAIEmbed(common::ObIAllocator &alloc)
    : ObFuncExprOperator(alloc, 
                        T_FUN_SYS_AI_EMBED, 
                        N_AI_EMBED, 
                        MORE_THAN_ZERO,
                        NOT_VALID_FOR_GENERATED_COL, 
                        NOT_ROW_DIMENSION) 
{
}

ObExprAIEmbed::~ObExprAIEmbed() 
{
}

int ObExprAIEmbed::calc_result_typeN(ObExprResType &type,
                                     ObExprResType *types_stack,
                                     int64_t param_num,
                                     common::ObExprTypeCtx &type_ctx) const 
{
  UNUSED(type_ctx);
  UNUSED(types_stack);
  int ret = OB_SUCCESS;
  if (OB_UNLIKELY(param_num > 3 || param_num < 2)) {
    ObString func_name_(get_name());
    ret = OB_ERR_PARAM_SIZE;
    LOG_USER_ERROR(OB_ERR_PARAM_SIZE, func_name_.length(), func_name_.ptr());
  } else {
    types_stack[MODEL_IDX].set_calc_type(ObVarcharType);
    types_stack[MODEL_IDX].set_calc_collation_type(CS_TYPE_UTF8MB4_BIN);
    types_stack[CONTENT_IDX].set_calc_type(ObVarcharType);
    types_stack[CONTENT_IDX].set_calc_collation_type(CS_TYPE_UTF8MB4_BIN);
    if (param_num == 3) {
      if (ob_is_integer_type(types_stack[DIM_IDX].get_type())) {
        types_stack[DIM_IDX].set_calc_type(ObIntType);
        types_stack[DIM_IDX].set_precision(10);
        types_stack[DIM_IDX].set_scale(0);
      } else {
        ret = OB_INVALID_ARGUMENT;
        LOG_USER_ERROR(OB_INVALID_ARGUMENT, "ai_embed, dimension parameter must be an integer, not a decimal or float");
      }
    }
    type.set_varchar();
    type.set_collation_type(CS_TYPE_UTF8MB4_BIN);
    type.set_collation_level(CS_LEVEL_COERCIBLE);
  }
  return ret;
}

int ObExprAIEmbed::eval_ai_embed(const ObExpr &expr, ObEvalCtx &ctx, ObDatum &res) 
{
  INIT_SUCC(ret);
  ObDatum *arg_model_id = nullptr;
  ObDatum *arg_content = nullptr;
  ObDatum *arg_dim = nullptr;
  if (expr.arg_cnt_ == 3 ? OB_FAIL(expr.eval_param_value(ctx, arg_model_id, arg_content, arg_dim))
                         : OB_FAIL(expr.eval_param_value(ctx, arg_model_id, arg_content))) {
  } else if (arg_model_id->is_null() || arg_content->is_null()) {
    // A null input produces a null result. The other rows of the same batch
    // must not be disturbed by it.
    res.set_null();
  } else {
    ObEvalCtx::TempAllocGuard tmp_alloc_g(ctx);
    
    MultimodeAlloctor temp_allocator(tmp_alloc_g.get_allocator());
    lib::ObMallocHookAttrGuard malloc_guard(lib::ObMemAttr(N_AI_EMBED));
    ObAIFuncExprInfo *info = nullptr;
    share::ObAiModelEndpointInfo resolved_endpoint;
    const share::ObAiModelEndpointInfo *endpoint_info = &resolved_endpoint;
    query::ObIAiEndpointResolver *endpoint_resolver =
        ::oceanbase::share::server_service<::oceanbase::query::ObIAiEndpointResolver>();
    ObString model_id = arg_model_id->get_string();
    ObString content;
    if (OB_FAIL(ObTextStringHelper::read_real_string_data(
                    ctx.exec_ctx_, temp_allocator, *arg_content,
                    expr.args_[CONTENT_IDX]->datum_meta_,
                    expr.args_[CONTENT_IDX]->obj_meta_.has_lob_header(), content))) {
    } else if (model_id.empty() || content.empty()) {
      ret = OB_INVALID_ARGUMENT;
      LOG_USER_ERROR(OB_INVALID_ARGUMENT, "ai_embed, model id or input is empty");
      res.set_null();
    }
    int64_t dim = OB_AI_DEFAULT_EMBED_DIMENSION;
    // Only a dimension that the caller asked for has to be matched by the
    // answer, the default one is a hint for the model.
    int64_t validate_dim = 0;
    ObJsonInt *dim_json = nullptr;
    ObJsonObject *config = nullptr;
    if (OB_FAIL(ret)) {
    } else if (OB_NOT_NULL(arg_dim)) {
      dim = arg_dim->get_int();
      if (dim <= 0) {
        ret = OB_INVALID_ARGUMENT;
        LOG_USER_ERROR(OB_INVALID_ARGUMENT, "ai_embed, dimension parameter must be a positive integer");
        res.set_null();
      } else {
        validate_dim = dim;
      }
    }
    if (OB_SUCC(ret)) {
      // The dimension is always part of the request.
      if (OB_FAIL(ObAIFuncJsonUtils::get_json_object(temp_allocator, config))) {
      } else if (OB_FAIL(ObAIFuncJsonUtils::get_json_int(temp_allocator, dim, dim_json))) {
      } else if (OB_FAIL(config->add("dimensions", dim_json))) {
      }
    }
    if (OB_FAIL(ret)) {
    } else if (OB_FAIL(ObAIFuncUtils::get_ai_func_info(temp_allocator, model_id, info))) {
    } else if (OB_ISNULL(endpoint_resolver)) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_FAIL(endpoint_resolver->resolve_by_model_name(
                   model_id, temp_allocator, resolved_endpoint))) {
    } else {
      ObAIFuncModel model(temp_allocator, *info, *endpoint_info);
      ObString result;
      if (OB_FAIL(model.call_dense_embedding(content, config, result, validate_dim))) {
      } else if (OB_FAIL(ObAIFuncUtils::set_string_result(expr, ctx, res, result))) {
      }
    }
  }
  return ret;
}

int ObExprAIEmbed::cg_expr(ObExprCGCtx &expr_cg_ctx, 
                           const ObRawExpr &raw_expr,
                           ObExpr &rt_expr) const 
{
  int ret = OB_SUCCESS;
  // TODO: support schema version match in plan cache for ai func
  // const ObRawExpr *model_key = raw_expr.get_param_expr(0);
  // if (OB_NOT_NULL(model_key)
  //     && (model_key->is_static_scalar_const_expr() || model_key->is_const_expr())
  //     && model_key->get_expr_type() != T_OP_GET_USER_VAR &&
  //     OB_NOT_NULL(expr_cg_ctx.schema_guard_)) {
  //   ObIAllocator *allocator = expr_cg_ctx.allocator_;
  //   ObExecContext *exec_ctx = expr_cg_ctx.session_->get_cur_exec_ctx();
  //   ObObj const_data;
  //   bool got_data = false;
  //   ObAIFuncExprInfo *info = nullptr;
  //   if (OB_ISNULL(allocator)) {
  //     ret = OB_ERR_UNEXPECTED;
  //     LOG_WARN("allocator is null", K(ret));
  //   } else if (OB_FAIL(ObSQLUtils::calc_const_or_calculable_expr(exec_ctx,
  //                                                         model_key,
  //                                                         const_data,
  //                                                         got_data,
  //                                                         *allocator))) {
  //     LOG_WARN("failed to calc offset expr", K(ret));
  //   } else if (!got_data || const_data.is_null()) {
  //   } else if (OB_FAIL(ObAIFuncUtils::get_ai_func_info(*allocator, const_data.get_string(), *expr_cg_ctx.schema_guard_, info))) {
  //     LOG_WARN("failed to get ai func info", K(ret), K(const_data.get_string()));
  //   } else {
  //     rt_expr.extra_info_ = info;
  //   }
  // }

  if (OB_SUCC(ret)) {
    rt_expr.eval_func_ = ObExprAIEmbed::eval_ai_embed;
    rt_expr.eval_batch_func_ = ObExprAIEmbed::eval_batch_ai_embed;
  }
  return ret;
}

int ObExprAIEmbed::eval_batch_ai_embed(const ObExpr &expr,
                                        ObEvalCtx &ctx,
                                        const ObBitVector &skip,
                                        const int64_t size)
{
  int ret = OB_SUCCESS;
  ObDatum *res = expr.locate_batch_datums(ctx);
  ObBitVector &eval_flags = expr.get_evaluated_flags(ctx);
  ObEvalCtx::BatchInfoScopeGuard batch_info_guard(ctx);
  batch_info_guard.set_batch_size(size);
  ObEvalCtx::TempAllocGuard tmp_alloc_g(ctx);
  MultimodeAlloctor temp_allocator(tmp_alloc_g.get_allocator());
  lib::ObMallocHookAttrGuard malloc_guard(lib::ObMemAttr(N_AI_EMBED));

  ObDatumVector model_datum_vector;
  ObDatumVector content_datum_vector;
  ObDatumVector dim_datum_vector;
  const bool has_dim = (expr.arg_cnt_ == 3);

  if (has_dim) {
    ret = expr.eval_batch_param_value(ctx, skip, size,
                                      model_datum_vector,
                                      content_datum_vector,
                                      dim_datum_vector);
  } else {
    ret = expr.eval_batch_param_value(ctx, skip, size,
                                      model_datum_vector,
                                      content_datum_vector);
  }

  // The rows of one batch share the model and the dimension, otherwise the
  // requests cannot be merged.
  ObArray<int64_t> row_index;
  ObArray<ObString> contents;
  ObString model_id;
  bool model_id_set = false;
  int64_t dim = 0;
  bool dim_set = false;
  bool can_merge = true;

  // A constant argument is evaluated once and its datum vector holds a single
  // element, so it is read through the first slot for every row.
  const bool model_is_batch = expr.args_[MODEL_IDX]->is_batch_result();
  const bool content_is_batch = expr.args_[CONTENT_IDX]->is_batch_result();
  const bool dim_is_batch = has_dim && expr.args_[DIM_IDX]->is_batch_result();

  for (int64_t i = 0; OB_SUCC(ret) && i < size; i++) {
    if (skip.at(i) || eval_flags.at(i)) {
      continue;
    }
    ObDatum *model_datum = model_datum_vector.at(model_is_batch ? i : 0);
    ObDatum *content_datum = content_datum_vector.at(content_is_batch ? i : 0);
    if (OB_ISNULL(model_datum) || OB_ISNULL(content_datum)) {
      continue;
    }
    if (model_datum->is_null() || content_datum->is_null()) {
      // A null input produces a null result and is not sent to the model.
      res[i].set_null();
      eval_flags.set(i);
      continue;
    }
    ObString row_model_id = model_datum->get_string();
    if (!model_id_set) {
      model_id = row_model_id;
      model_id_set = true;
    } else if (model_id != row_model_id) {
      can_merge = false;
      break;
    }
    if (has_dim) {
      ObDatum *dim_datum = dim_datum_vector.at(dim_is_batch ? i : 0);
      if (OB_ISNULL(dim_datum) || dim_datum->is_null()) {
        can_merge = false;
        break;
      }
      int64_t row_dim = dim_datum->get_int();
      if (!dim_set) {
        dim = row_dim;
        dim_set = true;
      } else if (dim != row_dim) {
        can_merge = false;
        break;
      }
    }
    if (OB_FAIL(row_index.push_back(i))) {
    } else {
      ObString content;
      // A LOB backed column keeps a locator inside the datum, the real
      // content has to be read explicitly.
      if (OB_FAIL(ObTextStringHelper::read_real_string_data(
                      ctx.exec_ctx_, temp_allocator, *content_datum,
                      expr.args_[CONTENT_IDX]->datum_meta_,
                      expr.args_[CONTENT_IDX]->obj_meta_.has_lob_header(),
                      content))) {
      } else if (OB_FAIL(contents.push_back(content))) {
      }
    }
  }

  if (OB_SUCC(ret) && !can_merge) {
    // The rows do not share the model, fall back to the row by row path.
    for (int64_t i = 0; OB_SUCC(ret) && i < size; i++) {
      if (skip.at(i) || eval_flags.at(i)) {
        continue;
      }
      batch_info_guard.set_batch_idx(i);
      if (OB_FAIL(expr.eval_func_(expr, ctx, res[i]))) {
      } else {
        eval_flags.set(i);
      }
    }
  } else if (OB_SUCC(ret) && !contents.empty()) {
    ObAIFuncExprInfo *info = nullptr;
    share::ObAiModelEndpointInfo resolved_endpoint;
    const share::ObAiModelEndpointInfo *endpoint_info = &resolved_endpoint;
    query::ObIAiEndpointResolver *endpoint_resolver =
        ::oceanbase::share::server_service<::oceanbase::query::ObIAiEndpointResolver>();
    ObJsonObject *config = nullptr;
    ObJsonInt *dim_json = nullptr;
    ObArray<ObString> results;
    // Only a dimension that the caller asked for has to be matched by the
    // answer, the default one is a hint for the model.
    const int64_t validate_dim = dim_set ? dim : 0;
    const int64_t send_dim = dim_set ? dim : OB_AI_DEFAULT_EMBED_DIMENSION;
    if (dim_set && dim <= 0) {
      ret = OB_INVALID_ARGUMENT;
      LOG_USER_ERROR(OB_INVALID_ARGUMENT, "ai_embed, dimension parameter must be a positive integer");
    } else if (model_id.empty()) {
      ret = OB_INVALID_ARGUMENT;
      LOG_USER_ERROR(OB_INVALID_ARGUMENT, "ai_embed, model id or input is empty");
    } else if (OB_FAIL(ObAIFuncUtils::get_ai_func_info(temp_allocator, model_id, info))) {
    } else if (OB_ISNULL(endpoint_resolver)) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_FAIL(endpoint_resolver->resolve_by_model_name(
                   model_id, temp_allocator, resolved_endpoint))) {
    } else if (OB_FAIL(ObAIFuncJsonUtils::get_json_object(temp_allocator, config))) {
    } else if (OB_FAIL(ObAIFuncJsonUtils::get_json_int(temp_allocator, send_dim, dim_json))) {
    } else if (OB_FAIL(config->add("dimensions", dim_json))) {
    } else {
      ObAIFuncModel model(temp_allocator, *info, *endpoint_info);
      // One request carries all the inputs of the batch.
      if (OB_FAIL(model.call_dense_embedding_vector_v2(contents, config, results, validate_dim))) {
      } else if (results.count() != contents.count()) {
        ret = OB_ERR_UNEXPECTED;
      } else {
        for (int64_t k = 0; OB_SUCC(ret) && k < results.count(); k++) {
          int64_t row = row_index[k];
          ObString result = results[k];
          // The result is copied into the memory of the row it belongs to.
          batch_info_guard.set_batch_idx(row);
          if (OB_FAIL(ObExprUtil::set_expr_ascii_result(expr, ctx, res[row], result))) {
          } else {
            eval_flags.set(row);
          }
        }
      }
    }
  }
  return ret;
}

} // namespace sql
} // namespace oceanbase
