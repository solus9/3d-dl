from keras.applications.inception_v3 import InceptionV3
from keras.preprocessing import image
from keras.models import Model
from keras.models import load_model
from keras.layers import Dense, GlobalAveragePooling2D, Dropout
from keras import backend as K
from time import *
import os


# For Function to feed images to model and augment images at the same time
from keras.preprocessing.image import ImageDataGenerator

# For Tensorboard & ValAccHistory
from keras.callbacks import TensorBoard, Callback

# for add_salt_pepper_noise
import numpy as np

# for leaving the program in case of invalid arguments (sys.exit(0))
import sys

# for get_config
from keras.models import Sequential

# for unzipping
import zipfile

# for customizing SGD, rmsprop
from keras.optimizers import SGD, RMSprop

# for logging
from pathlib import Path
import datetime

# for BO
from bayes_opt import BayesianOptimization

# Custom Image Augmentation Function
def add_salt_pepper_noise(X_img):
    # Need to produce a copy as to not modify the original image
    X_img_copy = X_img.copy()
    row, col, _ = X_img_copy.shape
    salt_vs_pepper = 0.2
    amount = 0.004
    num_salt = np.ceil(amount * X_img_copy.size * salt_vs_pepper)
    num_pepper = np.ceil(amount * X_img_copy.size * (1.0 - salt_vs_pepper))

    # Add Salt noise
    coords = [np.random.randint(0, i - 1, int(num_salt)) for i in X_img.shape]
    X_img[coords[0], coords[1], :] = 1

    # Add Pepper noise

    coords = [np.random.randint(0, i - 1, int(num_pepper)) for i in X_img.shape]
    X_img[coords[0], coords[1], :] = 0
    return X_img_copy

class ValAccHistory(Callback):
    def on_train_begin(self, logs={}):
        self.val_accs = []

    def on_epoch_end(self, epoch, logs={}):
        self.val_accs.append(logs.get('val_acc'))

class KerasInception:
    model = None
    input_dim = 0
    batch_size = 0
    dense_layers = 0

    def __init__(self,input_dim=150,batch_size=16,dense_layers=1,dropout=None,lr=0.001):
        self.input_dim = input_dim
        self.batch_size = batch_size
        self.dense_layers = dense_layers
        self.dropout = dropout
        self.lr = lr

    def assemble_model(self,train_dir):
        class_count = len(next(os.walk(train_dir))[1])

        # base pre-trained model
        base_model = InceptionV3(weights='imagenet', include_top=False)

        # global spatial average pooling layer
        x = base_model.output

        base_model.layers[-1].name = 'base_output'

        x = GlobalAveragePooling2D(name='pooling')(x)

        for i in range(self.dense_layers):
            # dropout
            if self.dropout and i == 0:
                x = Dropout(self.dropout)(x)
            elif self.dropout:
                x = Dropout(self.dropout)(x)
            #
            # fully-connected layer
            x = Dense(1024, activation='relu',name='dense'+str(i))(x)

        # logistic layer
        predictions = Dense(class_count, activation='softmax',name='softmax')(x)

        # this is the model we will train
        model = Model(inputs=base_model.input, outputs=predictions)

        # we want to train top layers only
        for layer in base_model.layers:
            layer.trainable = False

        # compile the model (*after* setting layers to non-trainable)
        model.compile(optimizer=RMSprop(lr=self.lr), loss='categorical_crossentropy', metrics=['accuracy'])

        return model

    # print train classes to txt file in classes_txt_dir
    def save_class_list(self,train_dir,classes_txt_dir):
        # assemble path
        filename = "classes.txt"
        my_file = os.path.join(classes_txt_dir, filename)
        print("Writing classes.txt to:\n",my_file,'\n')
        print("Classes found:")
        for name in os.listdir(train_dir):
            if not os.path.isfile(name):
                print(name)

        # check if file already exists
        if not os.path.isfile(my_file):
            # write all folder names to txt file
            with open(my_file, "w") as classes_file:
                for name in os.listdir(train_dir):
                    # exclude files
                    if not os.path.isfile(name):
                        classes_file.write(name)
                        classes_file.write("\n")
            classes_file.close()

    def train(self,train_dir,validation_dir,epochs=5,fine_tune=False,
            salt_pepper=False,augmentation_params={},classes_txt_dir=None,save_model=False):
        if classes_txt_dir:
            self.save_class_list(train_dir,classes_txt_dir)

        # model can only be built here after training directory is clear
        # (for number of classes)
        self.model = self.assemble_model(train_dir)

        print("Directory used for training: ",train_dir)
        print("Directory used for validation: ",validation_dir)

        # augmentation configuration for training
        if salt_pepper:
            train_datagen = ImageDataGenerator(
                    rescale=1./255,
                    preprocessing_function=add_salt_pepper_noise,
                    # horizontal_flip=False, # no flippin groceries
                    **augmentation_params)
        else:
            train_datagen = ImageDataGenerator(
                    rescale=1./255,
                    **augmentation_params)

        # generator that will read pictures found in train_dir, and
        # indefinitely generate batches of augmented image data and
        # rescales images to target_size, splits them into batches
        # (instead of loading all images directly into GPU memory)
        train_generator = train_datagen.flow_from_directory(
                train_dir,  # this is the target directory
                target_size=(self.input_dim, self.input_dim),  # all images will be resized to input_dimxinput_dim
                batch_size=self.batch_size,
                class_mode='categorical')

        # augmentation configuration for validation: only rescaling
        validation_datagen = ImageDataGenerator(rescale=1./255)

        # generator for validation data
        # similar to above but based on different augmentation function (above)
        validation_generator = validation_datagen.flow_from_directory(
                validation_dir,
                target_size=(self.input_dim, self.input_dim),
                batch_size=self.batch_size,
                class_mode='categorical')

        # log everything in tensorboard
        tensorboard = TensorBoard(log_dir="logs/{}".format(time()),
                            histogram_freq=0,
                            batch_size=32,
                            write_graph=True,
                            write_grads=False,
                            write_images=True,
                            embeddings_freq=0,
                            embeddings_layer_names=None,
                            embeddings_metadata=None) # histogram_freq=5

        history = ValAccHistory()

        # train the model on the new data for a few epochs
        self.model.fit_generator(
                train_generator,
                steps_per_epoch=2000 // self.batch_size,
                epochs=epochs,
                validation_data=validation_generator,
                validation_steps=800 // self.batch_size,
                callbacks = [tensorboard,history],
                use_multiprocessing=True, # not sure if working properly!
                workers=8)

        # print(self.model.get_config())

        if fine_tune:
            self.fine_tune(train_generator,validation_generator,tensorboard)

        if save_model:
            base_path,train_folder = os.path.split(train_dir)
            full_path = os.path.join(base_path, "model.h5")
            self.save_model(full_path)

        return history

    def fine_tune(self,train_generator,validation_generator,tensorboard):
        # we chose to train the top 2 inception blocks, i.e. we will freeze
        # the first 249 layers and unfreeze the rest:
        for layer in self.model.layers[:249]:
           layer.trainable = False
        for layer in self.model.layers[249:]:
           layer.trainable = True

        # we need to recompile the model for these modifications to take effect
        # we use SGD with a low learning rate
        self.model.compile(optimizer=SGD(lr=0.0001, momentum=0.9), loss='categorical_crossentropy', metrics=['accuracy'])

        # we train our model again (this time fine-tuning the top 2 inception blocks
        # alongside the top Dense layers
        self.model.fit_generator(
                train_generator,
                steps_per_epoch=2000 // self.batch_size,
                epochs=5,
                validation_data=validation_generator,
                validation_steps=800 // self.batch_size,
                callbacks = [tensorboard])


    def evaluate(self,test_dir):
        # augmentation configuration for testing: only rescaling
        test_datagen = ImageDataGenerator(rescale=1./255)

        # generator for test data
        # similar to above but based on different augmentation function (above)
        test_generator = test_datagen.flow_from_directory(
                test_dir,
                target_size=(self.input_dim, self.input_dim),
                batch_size=16,
                class_mode='categorical')

        score = self.model.evaluate_generator(test_generator)
        print('Test loss:', score[0])
        print('Test accuracy:', score[1])

        return score

    # expects a path to a model in h5 format (a model, not weights!)
    # model will be as when saving (i.e. compiled), can then call predict etc
    def load_model(self,file_path):
        self.model = load_model(file_path)

    def save_model(self,path):
        self.model.save(path)

def get_augmentation_params(augmentation_mode):
    if augmentation_mode == 0:
        return {}
    elif augmentation_mode == 1:
        return {'rotation_range': 180}
    elif augmentation_mode == 2:
        return {'rotation_range': 180, 'zoom_range': 0.2}
    else:
        print("UNKNOWN AUGMENTATION PARAMETER! (needs to be 0, 1 or 2)")
        sys.exit(0)

def unzip_and_return_path_to_folder(path_to_zip_file):
    maindirname, filename = os.path.split(path_to_zip_file)

    new_dir = os.path.join(maindirname, filename.split('.')[0])
    if not os.path.exists(new_dir):
        os.makedirs(new_dir)

    zip_ref = zipfile.ZipFile(path_to_zip_file, 'r')
    zip_ref.extractall(new_dir)
    zip_ref.close()

    return path_to_zip_file.split('.')[0] # name of new folder

def main():
    train_dir = '/vol/project/2017/530/g1753002/keras_test_data/train'
    validation_dir = '/vol/project/2017/530/g1753002/keras_test_data/validation'
    test_dir = '/vol/project/2017/530/g1753002/keras_test_data/test'
    dense_layers = 1
    input_dim = 150
    batch_size = 16
    fine_tune = False # if true, some of the inceptionV3 layers will be trained for 5 epochs at the end of training
    add_salt_pepper_noise = False # if True, it adds SP noise
    augmentation_mode = 0 # 0 = no augmentation, 1 = rotation only, 2 = rotation & zoom
    epochs = 1

    model = KerasInception(input_dim=input_dim,
                            batch_size=batch_size,
                            dense_layers=dense_layers,
                            lr=learning_rate)


    model.train(train_dir=train_dir,
                validation_dir=validation_dir,
                fine_tune=fine_tune,
                epochs=epochs,
                salt_pepper=add_salt_pepper_noise,
                augmentation_params=get_augmentation_params(augmentation_mode),
                classes_txt_dir="/homes/sk5317/")

    model.evaluate(test_dir=test_dir)

def main_for_pipeline():
    logging = True
    log_filename = 'model_log_full_w_dropout_firstlayer20.csv'

    # get all zip files to iterate over: List parameter, Directory?


    # create grid of parameters: e.g. to a csv file, then read out line by line, once done, add 1 at the end
    learning_rate_grid = np.logspace(-6,-3,10) # originally 10 pow -5
    dropout_grid = [0,0.2,0.5]
    layer_grid = [1,2]
    batch_size_grid = [16,32,64]

    # input directories
    path_of_zip = '/vol/project/2017/530/g1753002/keras_test_data/train/train_test_zip2.zip'
    validation_dir = '/vol/project/2017/530/g1753002/keras_test_data/validation'
    test_dir = '/vol/project/2017/530/g1753002/keras_test_data/test'

    # load train images from one zip file
    unzipped_dir = unzip_and_return_path_to_folder(path_of_zip)
    train_dir = unzipped_dir + '/images'

    # get path for classes.txt
    main_dir, filename = os.path.split(path_of_zip)

    # go through grid of parameters
    for lr in learning_rate_grid:


        # set parameters
        input_dim = 299
        fine_tune = False # if true, some of the inceptionV3 layers will be trained for 5 epochs at the end of training
        add_salt_pepper_noise = False # if True, it adds SP noise
        augmentation_mode = 0 # 0 = no augmentation, 1 = rotation only, 2 = rotation & zoom
        epochs = 10

        learning_rate = lr
        dense_layers = 2
        batch_size = 64
        dropout = 0.5

        # initialize & train model
        model = KerasInception(input_dim=input_dim,
                                batch_size=batch_size,
                                dense_layers=dense_layers,
                                dropout=dropout,
                                lr=learning_rate)


        model.train(train_dir=train_dir,
                    validation_dir=validation_dir,
                    fine_tune=fine_tune,
                    epochs=epochs,
                    salt_pepper=add_salt_pepper_noise,
                    augmentation_params=get_augmentation_params(augmentation_mode),
                    classes_txt_dir=main_dir,
                    save_model=True
                    )

        # get accuracy score
        score = model.evaluate(test_dir=test_dir)

        # store accuracy & model parameters
        if logging:
            print("logging now...")
            my_file = Path(log_filename)

            # write header if this is the first run
            if not my_file.is_file():
                print("writing head")
                with open(log_filename, "w") as log:
                    log.write("datetime,epochs,learning_rate,batch_size,input_dim,dense_layers,dropout,score[0],score[1]\n")

            # append parameters
            with open(log_filename, "a") as log:
                log.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
                log.write(',')
                log.write(str(epochs))
                log.write(',')
                log.write(str(learning_rate))
                log.write(',')
                log.write(str(batch_size))
                log.write(',')
                log.write(str(input_dim))
                log.write(',')
                log.write(str(dense_layers))
                log.write(',')
                log.write(str(dropout))
                log.write(',')
                log.write(str(score[0]))
                log.write(',')
                log.write(str(score[1]))
                log.write('\n')

def train_model(learning_rate,dense_layers,batch_size,dropout):
    # turn float inputs from BO to ints
    dense_layers = int(dense_layers + 0.5)
    batch_size = int(batch_size + 0.5)*16

    logging = True
    log_filename = 'log_bo_2.csv'

    # input directories
    path_of_zip = '/vol/project/2017/530/g1753002/keras_test_data/train/train_test_zip2.zip'
    validation_dir = '/vol/project/2017/530/g1753002/keras_test_data/validation'
    test_dir = '/vol/project/2017/530/g1753002/keras_test_data/test'

    # load train images from one zip file
    unzipped_dir = unzip_and_return_path_to_folder(path_of_zip)
    train_dir = unzipped_dir + '/images'

    # get path for classes.txt
    main_dir, filename = os.path.split(path_of_zip)

    # set parameters
    fine_tune = False # if true, some of the inceptionV3 layers will be trained for 5 epochs at the end of training
    add_salt_pepper_noise = False # if True, it adds SP noise
    augmentation_mode = 0 # 0 = no augmentation, 1 = rotation only, 2 = rotation & zoom
    epochs = 3
    input_dim = 299

    # initialize & train model
    model = KerasInception(input_dim=input_dim,
                            batch_size=batch_size,
                            dense_layers=dense_layers,
                            dropout=dropout,
                            lr=learning_rate)


    history = model.train(train_dir=train_dir,
                validation_dir=validation_dir,
                fine_tune=fine_tune,
                epochs=epochs,
                salt_pepper=add_salt_pepper_noise,
                augmentation_params=get_augmentation_params(augmentation_mode),
                classes_txt_dir=main_dir,
                save_model=True
                )

    # print(history.val_accs)
    # print(max(history.val_accs))

    # get accuracy score
    score = model.evaluate(test_dir=test_dir)
    print(score[1])
    # test_accuracy = score[1]

    # store accuracy & model parameters
    if logging:
        print("logging now...")
        my_file = Path(log_filename)

        # write header if this is the first run
        if not my_file.is_file():
            print("writing head")
            with open(log_filename, "w") as log:
                log.write("datetime,epochs,learning_rate,batch_size,input_dim,dense_layers,dropout,best_validation_accuracy\n")

        # append parameters
        with open(log_filename, "a") as log:
            log.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            log.write(',')
            log.write(str(epochs))
            log.write(',')
            log.write(str(learning_rate))
            log.write(',')
            log.write(str(batch_size))
            log.write(',')
            log.write(str(input_dim))
            log.write(',')
            log.write(str(dense_layers))
            log.write(',')
            log.write(str(dropout))
            log.write(',')
            log.write(str(max(history.val_accs)))
            log.write(',')
            log.write('\n')

    return max(history.val_accs) # return best validation accuracy

def bayes_optimization():
    gp_params = {"alpha": 1e-5}
    nnBO = BayesianOptimization(train_model,
        {'learning_rate': (1e-07, 1e-01),
        'batch_size': (0.5001, 4.4999),
        'dropout': (0, 0.5),
        'dense_layers': (0.5001, 3.4999)}
        )
    nnBO.explore({'learning_rate': [1.1787686347935867e-05],
            'dropout': [0.0],
            'dense_layers': [2.0],
            'batch_size': [4.5]
            })

    nnBO.maximize(init_points=5, n_iter=15, kappa=2)

    print(nnBO.res['max'])


# main()

main_for_pipeline()

# bayes_optimization()
