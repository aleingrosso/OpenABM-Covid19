/* covid19.i */
%module covid19
%{
#define SWIG_FILE_WITH_INIT
#define SWIG_PYTHON_CAST_MODE
#include "model.h"
#include "params.h"
#include "constant.h"
#include "input.h"
#include "individual.h"
#include "utilities.h"
#include "disease.h"
%}

%rename (create_model) new_model(parameters *params);
%rename (create_event) new_event(model *pmodel);

%nodefaultdtor;

%include "model.h"
%include "params.h"
%include "constant.h"
%include "input.h"
%include "individual.h"
%include "utilities.h"
%include "disease.h"
%include model_utils.i 
%include params_utils.i
%include interventions_utils.i
