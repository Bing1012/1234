from torch.utils.data import DataLoader

from utils.utils import *
from utils.train_eval import *

logger = logging.getLogger(__name__)

MODEL_CLASSES = {
    'bert':  (convert_examples_to_features, BuildDataSet,
             model_train, model_evaluate),
    'bert_sentence': (convert_examples_to_features_sentence, BuildDataSetSentence,
                      model_train_sentence, model_evaluate_sentence),
    'multi_bert': (convert_examples_to_features, BuildDataSetMultiTask,
                   model_multi_train, model_multi_evaluate),
}


class KFoldDataLoader(object):

    def __init__(self, examples, nums=5):
        self.max_nums = nums
        self.cur_nums = 0

        self.examples_key = []  # 'sentences1 ...'
        self.examples_dict = {}  # 'sentences1: [[], []...]'
        self.creat_group_dict(examples)

        np.random.shuffle(self.examples_key)
        self.group_lens = len(self.examples_key)
        self.step = self.group_lens // nums

    def creat_group_dict(self, examples):

        for example in examples:
            exists = self.examples_dict.get(example[0], None)
            if exists is None:
                self.examples_key.append(example[0])
                self.examples_dict[example[0]] = [example]
            else:
                self.examples_dict[example[0]].append(example)

    def __iter__(self):
        return self

    def __next__(self):
        if self.cur_nums < self.max_nums:
            if self.cur_nums == 0:
                dev_key = self.examples_key[:self.step]
                train_key = self.examples_key[self.step:]
            elif self.cur_nums == self.max_nums - 1:
                train_key = self.examples_key[:self.cur_nums * self.step]
                dev_key = self.examples_key[self.cur_nums * self.step:]
            else:
                train_key = self.examples_key[:self.cur_nums * self.step] + \
                             self.examples_key[(self.cur_nums + 1) * self.step:]
                dev_key = self.examples_key[self.cur_nums * self.step:(self.cur_nums + 1) * self.step]

            train_data = []
            dev_data = []
            for train in train_key:
                train_data.extend(self.examples_dict[train])
            for dev in dev_key:
                dev_data.extend(self.examples_dict[dev])
            np.random.shuffle(train_data)
            np.random.shuffle(dev_data)
            self.cur_nums += 1

            return train_data, dev_data
        else:
            raise StopIteration


def train_dev_test(
    config,
    train_data,
    model,
    tokenizer,
    train_enhancement=None,
    dev_data=None,
    test_examples=None,
):
    dev_acc = 0.
    predict_label = []

    # ????????????
    model_example = copy.deepcopy(model).to(config.device)

    # ????????????
    if train_enhancement:
        logger.info('????????????????????????????????????: %d', len(train_enhancement))
        train_data.extend(train_enhancement)

    config.train_num_examples = len(train_data)
    # ????????????
    convert_to_features, build_data_set, train_module, evaluate_module = MODEL_CLASSES[config.use_model]
    train_features = convert_to_features(
        examples=train_data,
        tokenizer=tokenizer,
        label_list=config.class_list,
        second_label_list=config.multi_class_list if config.multi_loss_tag else None,
        max_length=config.pad_size,
        data_type='train'
    )
    train_dataset = build_data_set(train_features)
    train_loader = DataLoader(train_dataset, batch_size=config.batch_size, shuffle=True)

    # dev ?????????????????????
    if dev_data is not None:
        config.dev_num_examples = len(dev_data)
        dev_features = convert_to_features(
            examples=dev_data,
            tokenizer=tokenizer,
            label_list=config.class_list,
            second_label_list=config.multi_class_list if config.multi_loss_tag else None,
            max_length=config.pad_size,
            data_type='dev'
        )
        dev_dataset = build_data_set(dev_features)
        dev_loader = DataLoader(dev_dataset, batch_size=config.batch_size, shuffle=True)
    else:
        dev_loader = None

    best_model = train_module(config, model_example, train_loader, dev_loader)

    if dev_data is not None:
        dev_acc, dev_loss, total_inputs_err = evaluate_module(config, best_model, dev_loader)
        logger.info('classify error sentences:')
        # for idx, error_dict in enumerate(total_inputs_err):
        #     tokens = tokenizer.convert_ids_to_tokens(error_dict['sentence_ids'], skip_special_tokens=True)
        #     logger.info('## idx: {}'.format(idx+1))
        #     logger.info('sentences: {}.'.format(''.join(x for x in tokens)))
        #     logger.info('true label: {}'.format(error_dict['true_label']))
        #     logger.info('proba: {}'.format(error_dict['proba']))

        logger.info('evaluate: acc: {0:>6.2%}, loss: {1:>.6f}'.format(dev_acc, dev_loss))

    if test_examples is None and dev_data is not None:
        test_examples = dev_data

        test_features = convert_to_features(
            examples=test_examples,
            tokenizer=tokenizer,
            label_list=config.class_list,
            second_label_list=config.multi_class_list if config.multi_loss_tag else None,
            max_length=config.pad_size,
            data_type='test'
        )
        test_dataset = build_data_set(test_features)
        test_loader = DataLoader(test_dataset, batch_size=config.batch_size, shuffle=False)
        predict_label = evaluate_module(config, model_example, test_loader, test=True)

    return best_model, dev_acc, predict_label


def k_fold_cross_validation(
        config,
        train_examples,
        model,
        tokenizer,
        train_enhancement=None,
        test_examples=None,
        save_model=False,
):
    """
    :param config:
    :param train_examples:
    :param model:
    :param tokenizer:
    :param train_enhancement: ????????????????????????????????????train???????????????????????????train_examples????????????
    :param test_examples:
    :param save_model:
    :return: dev_evaluate : list [dev_acc,...]
             k_fold_predict_label : list. if not test_examples, k-fold predict on test.
    """
    dev_evaluate = []
    dev_predict_set = []
    k_fold_loader = KFoldDataLoader(train_examples, nums=config.k_fold)
    idx = 0
    for train_data, dev_data in k_fold_loader:
        idx += 1
        logger.info('k-fold CrossValidation: # %d', idx)
        best_model, dev_acc, predict_label = train_dev_test(
            config=config,
            train_data=train_data,
            model=model,
            tokenizer=tokenizer,
            train_enhancement=train_enhancement,
            dev_data=dev_data,
            test_examples=test_examples)
        dev_predict_set.append(predict_label)
        # ????????????
        if config.device.type == 'gpu':
            torch.cuda.empty_cache()
        dev_evaluate.append(dev_acc)

        if save_model:
            model_save(config, best_model, config.models_name+'_'+str(idx))

    logger.info('K({}) models dev acc mean: {}'.format(idx, np.array(dev_evaluate).mean()))

    return dev_evaluate, dev_predict_set


def cross_validation(
        config,
        train_examples,
        dev_examples,
        model,
        tokenizer,
        pattern='k_fold',
        train_enhancement=None,
        test_examples=None,
):
    if pattern == 'k_fold':
        train_examples.extend(dev_examples)
        dev_evaluate, _ = k_fold_cross_validation(
            config, train_examples, model, tokenizer,
            train_enhancement=train_enhancement,
            test_examples=None,
        )
        return None, dev_evaluate, None
    elif pattern == 'k_volt':
        dev_evaluate, dev_predict_set = k_fold_cross_validation(
            config, train_examples, model, tokenizer,
            train_enhancement=train_enhancement,
            test_examples=test_examples
        )
        return None, dev_evaluate, dev_predict_set
    elif pattern == 'k_volt_submit':
        train_examples.extend(dev_examples)
        dev_evaluate, dev_predict_set = k_fold_cross_validation(
            config, train_examples, model, tokenizer,
            train_enhancement=train_enhancement,
            test_examples=None,
            save_model=True
        )
        return None, dev_evaluate, dev_predict_set
    elif pattern == 'full_train':
        train_examples.extend(dev_examples)
        model_example, _, _ = train_dev_test(
            config=config,
            train_data=train_examples,
            model=model,
            tokenizer=tokenizer,
            train_enhancement=train_enhancement,
            dev_data=dev_examples,
            test_examples=None)
        return model_example, None, None
    elif pattern == 'predict':
        model_example, dev_acc, predict_label = train_dev_test(
            config=config,
            train_data=train_examples,
            model=model,
            tokenizer=tokenizer,
            train_enhancement=train_enhancement,
            dev_data=dev_examples,
            test_examples=test_examples)
        return model_example, dev_acc, predict_label




